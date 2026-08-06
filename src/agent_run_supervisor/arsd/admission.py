"""Durable principal-scoped admission: derivation, digest, prepare, finalize.

Pure admission helpers plus the write-once submission artifact contract used by
``arsd.handlers``. No sockets. The sole ``create_run`` site for socket-submitted
Runs is ``prepare_run``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

from agent_run_supervisor.event_store import EventStore, RunHandle, _RUN_ID_RE
from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp import agent_registry, storage
from agent_run_supervisor.native_acp.agent_registration import AgentEntry
from agent_run_supervisor.result import build_result_payload
from agent_run_supervisor.session import derive_session_id_for_run, is_valid_session_id

from . import protocol

# Moved with the reset: the digest material genuinely changed, because agent
# identity replaced profile selection and the launch material became
# value-blind. A pre-reset frame therefore hashes differently by construction
# rather than being silently reinterpreted.
DIGEST_SCHEMA_VERSION = 3
SUBMISSION_SCHEMA_VERSION = 3

# Request fields that would let a caller choose a runtime, name a value, or
# anticipate a transport. None of them exists on ``AgentRunRequest`` and none is
# added here: the guard is a structural assertion that admission never grew one,
# not a filter over caller input.
FORBIDDEN_RUNTIME_SELECTION_FIELDS = (
    "runtime_path",
    "executable",
    "command",
    "argv",
    "args",
    "env",
    "environment",
    "env_overlay",
    "env_passthrough",
    "cli_path",
    "cli_version",
    "cli_sha256",
    "sha256",
    "digest",
    "binding_generation_id",
    "generation_id",
    "adapter_contract_hash",
    "agent_registration_hash",
    "session_compatibility_epoch",
    "profile_id",
    "mediation",
    "secret",
    "secret_refs",
    "transport",
    "endpoint",
    "attach",
    "remote",
)

# No request field leaves the digest material. The one omission that used to
# exist was a compatibility measure for a wire that had not yet moved; the
# schema version moves instead, which is the honest way to say the material
# changed.
_DIGEST_OMIT_WHEN_NONE: tuple[str, ...] = ()

_RUN_ID_PREFIX = "run-"
_DERIVATION_TAG = b"arsd-run-id-v1\x00"
_REQUEST_ID_RE = re.compile(rf"[A-Za-z0-9._-]{{1,{protocol.MAX_REQUEST_ID_CHARS}}}")


@dataclasses.dataclass(frozen=True)
class AdmissionKey:
    """Principal-scoped idempotency key for submit."""

    principal_id: str
    request_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.principal_id, str) or not self.principal_id:
            raise ValueError("principal_id must be a non-empty string")
        if (
            not isinstance(self.request_id, str)
            or _REQUEST_ID_RE.fullmatch(self.request_id) is None
        ):
            raise ValueError(
                f"request_id must be 1..{protocol.MAX_REQUEST_ID_CHARS} "
                "characters from [A-Za-z0-9._-]"
            )


@dataclasses.dataclass(frozen=True)
class RequestDigest:
    value: str
    prompt_sha256: str
    prompt_bytes: int


def is_safe_run_id(run_id: str) -> bool:
    """EventStore-safe run id that cannot escape the event-store root."""
    if not isinstance(run_id, str) or not run_id:
        return False
    if _RUN_ID_RE.fullmatch(run_id) is None:
        return False
    if run_id in {".", ".."} or ".." in run_id:
        return False
    if run_id.startswith("/") or run_id.startswith("\\"):
        return False
    if "\x00" in run_id:
        return False
    return True


def derive_run_id(key: AdmissionKey) -> str:
    """Deterministic EventStore-safe Run identity for ``(principal_id, request_id)``.

    Encoding is tagged and length-prefixed so field-boundary shifts cannot collide.
    """
    principal = key.principal_id.encode("utf-8")
    request = key.request_id.encode("utf-8")
    material = (
        _DERIVATION_TAG
        + len(principal).to_bytes(8, "big")
        + principal
        + len(request).to_bytes(8, "big")
        + request
    )
    return _RUN_ID_PREFIX + hashlib.sha256(material).hexdigest()[:32]


def derive_session_id(key: AdmissionKey) -> str:
    """The prospective Session identity of a **create** submission.

    A pure function of the same authenticated identity that derives the Run, so
    a repeated request converges on the same Session instead of creating a
    second one and a lost response cannot split a caller's context. Nothing
    durable about the Session is written under this id before ``session/new``:
    the sealed submission/Spec is the whole reservation.

    The rule itself lives in :func:`session.derive_session_id_for_run` — one
    definition shared by admission, the durable submission validator, and the
    runtime — and this is its admission-key-facing spelling.
    """
    return derive_session_id_for_run(derive_run_id(key))


def compute_request_digest(command: protocol.SubmitCommand) -> RequestDigest:
    """Canonical digest over every behavior-affecting submit input.

    Excludes transport-only material (``api_version``, ``op``, ``request_id``).
    Prompt is bound as SHA-256 + UTF-8 byte count, never as plaintext.

    ``DIGEST_SCHEMA_VERSION`` does **not** move when a request field is added:
    the omit-when-None discipline (``_DIGEST_OMIT_WHEN_NONE``) keeps a
    pre-upgrade frame's digest byte-identical, while a request that names an
    agent digests differently because it behaves differently.
    """
    prompt = command.prompt_text.encode("utf-8")
    prompt_sha256 = hashlib.sha256(prompt).hexdigest()
    request_material = dataclasses.asdict(command.request)
    for name in _DIGEST_OMIT_WHEN_NONE:
        if request_material.get(name) is None:
            request_material.pop(name, None)
    material = {
        "digest_schema_version": DIGEST_SCHEMA_VERSION,
        "request": request_material,
        "workspace_root": command.workspace_root,
        "cwd": command.cwd,
        "retry_of_run_id": command.retry_of_run_id,
        "prompt_sha256": prompt_sha256,
        "prompt_bytes": len(prompt),
    }
    canonical = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    value = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return RequestDigest(
        value=value, prompt_sha256=prompt_sha256, prompt_bytes=len(prompt)
    )


def prepare_run(event_store: EventStore, run_id: str) -> RunHandle:
    """Exclusive ``create_run`` for a derived Run identity (socket-submit only)."""
    return event_store.create_run(run_id)


def resolve_agent_entry(
    snapshot: agent_registry.AgentRegistrySnapshot | None, agent_id: str
) -> AgentEntry:
    """Resolve one Run's agent against the startup snapshot. **In memory only.**

    This is the whole per-Run agent resolution. It performs zero filesystem
    access: the registry was opened exactly once, at daemon startup, before the
    socket was bound, and the snapshot it produced is immutable. Two concurrent
    Runs therefore cannot resolve different registry contents, a serving daemon
    cannot be re-pointed, and a registry edit takes effect at the next daemon
    start rather than the next Run.

    ``agent_id`` *is* caller text, and it selects among operator-authored,
    source-bounded entries. It names no path, executable, argv, environment key,
    digest, or version, and the grammar that runs before the lookup makes it
    unable to.
    """
    return agent_registry.resolve_agent_entry(snapshot, agent_id)


def finalize_registration_failure(handle: RunHandle, run_id: str) -> None:
    """Write-once pre-dispatch ``failed`` terminal via the existing result builder."""
    payload = build_result_payload(
        run_id=run_id,
        status=AgentRunStatus.FAILED,
        origin="supervisor",
        detail_code="REGISTRATION_FAILED",
        retryable=False,
        signal=None,
        stop_reason=None,
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=handle.run_dir,
        raw_event_path="events.jsonl",
    )
    storage.write_once_json(handle.run_dir / "result.json", payload)


def build_submission_artifact(
    *,
    key: AdmissionKey,
    run_id: str,
    command: protocol.SubmitCommand,
    digest: RequestDigest,
    accepted_at: str,
    peer: Mapping[str, int],
) -> dict[str, Any]:
    """Exact §6 v1 submission field set — no prompt text, no secrets."""
    return {
        "schema_version": SUBMISSION_SCHEMA_VERSION,
        "principal_id": key.principal_id,
        "request_id": key.request_id,
        "run_id": run_id,
        "retry_of_run_id": command.retry_of_run_id,
        "api_version": protocol.ARSD_API_VERSION,
        "accepted_at": accepted_at,
        "peer": {
            "pid": int(peer["pid"]),
            "uid": int(peer["uid"]),
            "gid": int(peer["gid"]),
        },
        "owner": command.request.owner,
        "namespace": command.request.namespace,
        # Recorded exactly as the caller sent it: ``None`` is a create, whose
        # prospective Session id is derived from this record's own
        # principal/request identity rather than stored a second time.
        "session_id": command.request.session_id,
        "agent_id": command.request.agent_id,
        "request_digest": digest.value,
        "prompt_sha256": digest.prompt_sha256,
        "prompt_bytes": digest.prompt_bytes,
    }


def write_submission(run_dir: Path, artifact: Mapping[str, Any]) -> Path:
    return storage.write_once_json(Path(run_dir) / "submission.json", artifact)


SUBMISSION_NAME = "submission.json"

# The exact field set ``build_submission_artifact`` emits, named once so the
# writer and the strict validator cannot drift. A structural test asserts the
# builder's own output key set equals this tuple.
SUBMISSION_FIELDS = (
    "schema_version",
    "principal_id",
    "request_id",
    "run_id",
    "retry_of_run_id",
    "api_version",
    "accepted_at",
    "peer",
    "owner",
    "namespace",
    "session_id",
    "agent_id",
    "request_digest",
    "prompt_sha256",
    "prompt_bytes",
)
SUBMISSION_PEER_FIELDS = ("pid", "uid", "gid")


@dataclasses.dataclass(frozen=True)
class SubmissionAttribution:
    """Exact run/owner/namespace/Session identity carried by a valid submission.

    ``session_id`` is always resolved: the caller's value for a reuse, and the
    deterministic prospective id for a create. Reconciliation reads one field
    and never has to re-derive a rule.
    """

    run_id: str
    owner: str
    namespace: str
    session_id: str


@dataclasses.dataclass(frozen=True)
class SubmissionState:
    """Classification of the durable submission plus its attribution when valid."""

    kind: storage.JsonDocumentKind
    attribution: SubmissionAttribution | None = None


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_submission_artifact(
    payload: Any, *, run_id: str
) -> SubmissionAttribution | None:
    """Strict v1 submission validation, or ``None``.

    The single definition of "this submission record is usable evidence",
    shared by admission and reconciliation so neither can drift into a weaker
    reading. The document must carry **exactly** the field set the writer
    emits — an unknown key, a missing key, or nested ``peer`` drift means this
    is not a record ARS produced, and a document ARS did not produce is not
    evidence about a Run ARS admitted. Attribution is exact: the record must
    name **this** Run, and its Session identity is either the declared reuse id
    or the deterministic prospective id derived from this record's own
    principal/request identity — never a directory name, a result field, or
    anything inferred.
    """
    if not isinstance(payload, dict):
        return None
    if set(payload) != set(SUBMISSION_FIELDS):
        return None
    if payload.get("schema_version") != SUBMISSION_SCHEMA_VERSION:
        return None
    if not _nonempty_str(payload.get("agent_id")):
        return None
    if payload.get("run_id") != run_id or not _nonempty_str(run_id):
        return None
    if not _nonempty_str(payload.get("principal_id")):
        return None
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or _REQUEST_ID_RE.fullmatch(request_id) is None:
        return None
    owner = payload.get("owner")
    namespace = payload.get("namespace")
    if not _nonempty_str(owner) or not _nonempty_str(namespace):
        return None
    declared_session_id = payload.get("session_id")
    if declared_session_id is None:
        # A create: the prospective Session is named by the Run identity this
        # record attests — already checked equal to ``run_id`` above — through
        # the same single rule the runtime selector uses.
        session_id = derive_session_id_for_run(run_id)
    elif is_valid_session_id(declared_session_id):
        session_id = declared_session_id
    else:
        return None
    if not _nonempty_str(payload.get("request_digest")):
        return None
    if not _nonempty_str(payload.get("prompt_sha256")):
        return None
    if not _plain_int(payload.get("prompt_bytes")) or payload["prompt_bytes"] < 0:
        return None
    if not _plain_int(payload.get("api_version")):
        return None
    if not _nonempty_str(payload.get("accepted_at")):
        return None
    peer = payload.get("peer")
    if not isinstance(peer, dict) or set(peer) != set(SUBMISSION_PEER_FIELDS):
        return None
    if not all(_plain_int(peer.get(field)) for field in SUBMISSION_PEER_FIELDS):
        return None
    retry_of = payload.get("retry_of_run_id")
    if retry_of is not None and not _nonempty_str(retry_of):
        return None
    return SubmissionAttribution(
        run_id=run_id,
        owner=owner,
        namespace=namespace,
        session_id=session_id,
    )


def classify_submission(run_dir: Path, *, run_id: str) -> SubmissionState:
    """VALID / ABSENT / CORRUPT for the durable submission of one Run.

    A structurally readable document that fails strict validation is CORRUPT,
    not a weaker "valid enough" attribution source.
    """
    state = storage.classify_json_document(Path(run_dir) / SUBMISSION_NAME)
    if state.kind is not storage.JsonDocumentKind.VALID:
        return SubmissionState(state.kind)
    attribution = validate_submission_artifact(state.payload, run_id=run_id)
    if attribution is None:
        return SubmissionState(storage.JsonDocumentKind.CORRUPT)
    return SubmissionState(storage.JsonDocumentKind.VALID, attribution)


def read_submission(run_dir: Path) -> dict[str, Any] | None:
    path = Path(run_dir) / "submission.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != SUBMISSION_SCHEMA_VERSION:
        return None
    return payload


def submission_binds_key(submission: Mapping[str, Any], key: AdmissionKey) -> bool:
    return (
        submission.get("principal_id") == key.principal_id
        and submission.get("request_id") == key.request_id
    )


def has_terminal_result(run_dir: Path) -> bool:
    """True only when a TRUSTED Native terminal artifact is present."""
    state = inspect_terminal_result(run_dir)
    return state.kind is storage.NativeTerminalKind.TRUSTED


def inspect_terminal_result(
    run_dir: Path, *, run_id: str | None = None
) -> storage.NativeTerminalState:
    """Typed Native terminal inspection (ABSENT / TRUSTED / INVALID)."""
    run_dir = Path(run_dir)
    rid = run_id if isinstance(run_id, str) and run_id else run_dir.name
    return storage.read_native_terminal_result(run_dir / "result.json", run_id=rid)


def read_result(run_dir: Path) -> dict[str, Any] | None:
    """Return a TRUSTED Native terminal payload, else ``None`` (absent/invalid)."""
    state = inspect_terminal_result(run_dir)
    if state.kind is storage.NativeTerminalKind.TRUSTED:
        return state.payload
    return None


def bound_session_id_for_run(
    *, run_id: str, submission: Mapping[str, Any] | None
) -> str | None:
    """Resolve the Session bound to a Native run from its submission artifact.

    A reuse resolves to the id the caller sent; a create resolves to the
    prospective id derived from the Run identity by the single shared rule.
    """
    if submission is None:
        return None
    declared = submission.get("session_id")
    if declared is not None:
        return declared if is_valid_session_id(declared) else None
    try:
        return derive_session_id_for_run(run_id)
    except Exception:
        return None


def read_progress(run_dir: Path) -> dict[str, Any] | None:
    path = Path(run_dir) / "progress.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


class KeyedLocks:
    """In-process per-key admission locks; entries are removed when idle."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._waiters: dict[tuple[str, str], int] = {}

    def __len__(self) -> int:
        return len(self._locks)

    @asynccontextmanager
    async def hold(self, key: tuple[str, str]) -> AsyncIterator[None]:
        async with self._guard:
            lock = self._locks.setdefault(key, asyncio.Lock())
            self._waiters[key] = self._waiters.get(key, 0) + 1
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()
            async with self._guard:
                remaining = self._waiters.get(key, 1) - 1
                if remaining <= 0:
                    self._waiters.pop(key, None)
                    self._locks.pop(key, None)
                else:
                    self._waiters[key] = remaining

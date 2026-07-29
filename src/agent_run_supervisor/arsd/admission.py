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
from agent_run_supervisor.native_acp import runtime_binding, storage
from agent_run_supervisor.native_acp.profile import AgentProfile
from agent_run_supervisor.result import build_result_payload

from . import protocol

DIGEST_SCHEMA_VERSION = 1
SUBMISSION_SCHEMA_VERSION = 1

# Request fields that would let a caller choose a runtime. None of them exist
# on ``AgentRunRequest`` and none is added here: the guard is a structural
# assertion that admission never grew one, not a filter over caller input.
FORBIDDEN_RUNTIME_SELECTION_FIELDS = (
    "runtime_path",
    "executable",
    "cli_path",
    "cli_version",
    "cli_sha256",
    "binding_generation_id",
    "generation_id",
    "adapter_contract_hash",
    "session_compatibility_epoch",
)

# The closed set of request fields that leave the digest material when they are
# ``None`` — deliberately one named field, never a blanket null-strip.
#
# Production is live at the pre-change digest, so a legacy frame must hash
# byte-identically or every in-flight retry becomes an idempotency conflict for
# no information gain. A blanket strip would go the other way and collapse the
# meaningful existing nulls (``ars_session_id``, ``expected_binding_hash``,
# ``cwd``, ``retry_of_run_id``), changing every digest instead.
_DIGEST_OMIT_WHEN_NONE = ("agent_id",)

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


def resolve_runtime_binding(
    profile: AgentProfile,
    *,
    binding_root: Path | None,
    ownership: runtime_binding.TrustedOwnership | None = None,
    agent_id: str | None = None,
) -> runtime_binding.AdmittedRuntimeBinding | None:
    """The single per-Run Binding read (C8) — one pointer, one generation.

    Reads ``active.json`` exactly once and the generation it names exactly
    once, revalidates contract match and artifact digest against the trusted
    immutable paths, and hands the result forward as a sealed value. Nothing
    downstream re-opens the Binding root, so a promotion between two Runs can
    never re-point work already admitted.

    An agent-scoped profile reads one ``registration.json`` first — three reads
    total, still exactly once each — and the whole read set is anchored inside
    that agent's subtree.

    The profile comes from the closed registry and the root from operator
    supplied daemon configuration. ``agent_id`` *is* caller text, and it selects
    among operator-authored, source-bounded registrations exactly as
    ``profile_id`` selects among source-registered profiles: it names no path,
    executable, argv, env key, digest, or version, and the component grammar
    that runs before any filesystem query makes it unable to.

    A profile whose contract declares no slot needs no Binding and gets
    ``None``; a profile that declares slots refuses fail-closed when no root is
    configured, rather than falling back to a source constant that no longer
    exists.
    """
    if not profile.contract.requires_binding:
        return None
    if binding_root is None:
        raise runtime_binding.BindingRefusal(
            rule="BINDING_ROOT_NOT_CONFIGURED",
            message=(
                "runtime binding refused [BINDING_ROOT_NOT_CONFIGURED]: profile "
                f"{profile.profile_id} requires a Runtime Binding root and none "
                "is configured"
            ),
        )
    policy = ownership or runtime_binding.default_ownership()
    reader = runtime_binding.BindingReader(binding_root, ownership=policy)
    registration = (
        None if agent_id is None else reader.read_registration(profile, agent_id)
    )
    return runtime_binding.AdmittedRuntimeBinding(
        resolved=reader.resolve_active(profile, agent_id=agent_id),
        ownership=policy,
        registration=registration,
    )


def finalize_registration_failure(handle: RunHandle, run_id: str) -> None:
    """Write-once pre-dispatch ``failed`` terminal via the existing result builder."""
    payload = build_result_payload(
        run_id=run_id,
        status=AgentRunStatus.FAILED,
        origin="supervisor",
        detail_code="REGISTRATION_FAILED",
        retryable=False,
        exit_code=None,
        signal=None,
        stop_reason=None,
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=handle.run_dir,
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
        "session_reuse": command.request.session_reuse,
        "ars_session_id": command.request.ars_session_id,
        "profile_id": command.request.profile_id,
        "request_digest": digest.value,
        "prompt_sha256": digest.prompt_sha256,
        "prompt_bytes": digest.prompt_bytes,
    }


def write_submission(run_dir: Path, artifact: Mapping[str, Any]) -> Path:
    return storage.write_once_json(Path(run_dir) / "submission.json", artifact)


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
    """Resolve the Session bound to a Native run from its submission artifact."""
    if submission is None:
        return None
    reuse = submission.get("session_reuse")
    if reuse == "reuse":
        ars_session_id = submission.get("ars_session_id")
        return ars_session_id if isinstance(ars_session_id, str) else None
    return f"{run_id}-ephemeral"


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

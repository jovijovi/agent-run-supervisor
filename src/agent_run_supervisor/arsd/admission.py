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
from agent_run_supervisor.native_acp import storage
from agent_run_supervisor.result import build_result_payload

from . import protocol

DIGEST_SCHEMA_VERSION = 1
SUBMISSION_SCHEMA_VERSION = 1

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
    """
    prompt = command.prompt_text.encode("utf-8")
    prompt_sha256 = hashlib.sha256(prompt).hexdigest()
    material = {
        "digest_schema_version": DIGEST_SCHEMA_VERSION,
        "request": dataclasses.asdict(command.request),
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
    return (Path(run_dir) / "result.json").is_file()


def read_result(run_dir: Path) -> dict[str, Any] | None:
    path = Path(run_dir) / "result.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


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

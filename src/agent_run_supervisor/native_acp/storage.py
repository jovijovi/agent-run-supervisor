"""Store-isolation binding seam: the only Native root constructors.

Every Native session/run store binds here to ``native-sessions/`` and
``native-runs/`` under an already-resolved supervisor control root. The
helpers are deliberately root-binding constructors plus thin native-only
wrappers — not a storage abstraction. Direct ``SessionStore``/``EventStore``
construction anywhere else in ``native_acp/`` is a structural test failure;
legacy stores, roots, and defaults are never referenced or modified.
"""

from __future__ import annotations

import datetime as _dt
import enum
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent_run_supervisor.event_store import (
    EventStore,
    durable_secure_mkdir,
    exclusive_create_bytes,
)
from agent_run_supervisor.process_liveness import LivenessProbe
from agent_run_supervisor.redaction import SafeText
from agent_run_supervisor.result import (
    MAX_NATIVE_RESULT_SERIALIZED_BYTES,
    validate_native_terminal_result,
)
from agent_run_supervisor.session import SessionRecord, SessionStore

# The only place in src/ where these directory names are spelled.
NATIVE_SESSIONS_DIRNAME = "native-sessions"
NATIVE_RUNS_DIRNAME = "native-runs"

# Native state vocabulary bijection: the authority/API term 'active' maps to
# the existing on-disk compatibility value 'open' at this boundary; 'closed'
# and 'quarantined' persist 1:1. The persisted file never carries 'active'.
_TO_NATIVE_STATE = {"open": "active", "closed": "closed", "quarantined": "quarantined"}
_TO_PERSISTED_STATE = {
    "active": "open",
    "closed": "closed",
    "quarantined": "quarantined",
}

# Read cap for terminal evidence: schema ceiling plus decode bound.
_MAX_TERMINAL_READ_BYTES = MAX_NATIVE_RESULT_SERIALIZED_BYTES


# Read cap for the classifying reconciliation reader. Spec, launch, and
# submission documents are bounded records; anything larger is not one of them.
MAX_RECONCILE_JSON_BYTES = 1_048_576


class NativeTerminalKind(enum.Enum):
    """Typed outcomes for the single trusted Native terminal reader."""

    ABSENT = "absent"
    TRUSTED = "trusted"
    INVALID = "invalid"


class JsonDocumentKind(enum.Enum):
    """Three-way classification of a durable Native JSON artifact.

    ``ABSENT`` is reachable **only** from a clean no-such-path result. Every
    other present-or-indeterminate state is ``CORRUPT`` — never a second chance
    to become absent — because absent and corrupt select different
    reconciliation rows and only one of them is safe.
    """

    ABSENT = "absent"
    VALID = "valid"
    CORRUPT = "corrupt"


@dataclass(frozen=True)
class JsonDocumentState:
    """The classification and, only when VALID, the decoded object."""

    kind: JsonDocumentKind
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class NativeTerminalState:
    """ABSENT, TRUSTED(payload), or INVALID/UNCERTAIN — never raw errors/data."""

    kind: NativeTerminalKind
    payload: dict[str, Any] | None = None


def to_native_state(persisted: str) -> str:
    try:
        return _TO_NATIVE_STATE[persisted]
    except KeyError:
        raise ValueError(f"not a persisted native session state: {persisted!r}") from None


def to_persisted_state(native: str) -> str:
    try:
        return _TO_PERSISTED_STATE[native]
    except KeyError:
        raise ValueError(f"not a canonical native session state: {native!r}") from None


def native_session_store(
    supervisor_root: Path, *, liveness_probe: LivenessProbe | None = None
) -> SessionStore:
    """A SessionStore bound explicitly to ``<supervisor_root>/native-sessions``.

    Performs no discovery of its own: the caller passes the already-resolved
    supervisor control root. The root is created-or-verified at 0700 via the
    durable secure-mkdir primitive — a pre-existing insecure mode is
    deliberately corrected.
    """
    root = Path(supervisor_root) / NATIVE_SESSIONS_DIRNAME
    durable_secure_mkdir(root)
    return SessionStore(base_dir=root, liveness_probe=liveness_probe)


def native_event_store(supervisor_root: Path) -> EventStore:
    """An EventStore bound explicitly to ``<supervisor_root>/native-runs``.

    Secured at 0700 here, so ``EventStore.create_run``'s own plain mkdir never
    runs against an unverified root.
    """
    root = Path(supervisor_root) / NATIVE_RUNS_DIRNAME
    durable_secure_mkdir(root)
    return EventStore(base_dir=root)


def write_once_json(path: Path, payload: Mapping[str, Any]) -> Path:
    """The single sanctioned writer for immutable Native artifacts.

    Canonical JSON created exclusively at 0600 via ``exclusive_create_bytes``
    (temp + atomic no-clobber publish); a second create of the same path raises
    ``FileExistsError`` and can never overwrite the first bytes. Ordinary
    atomic replacement is *not* write-once and is never used for these.
    """
    data = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
    return exclusive_create_bytes(Path(path), data)


def write_run_text(handle: Any, name: str, value: SafeText) -> Path:
    """The only sanctioned Native writer for free-form Run text.

    The parameter type is the boundary: a bare ``str`` has not crossed
    :class:`~agent_run_supervisor.redaction.RunTextGuard` and is refused here
    rather than trusted because a call site looked careful. ``SafeText`` is
    constructible only inside ``redaction``, so the refusal cannot be talked
    around at this seam.
    """
    if type(value) is not SafeText:
        raise TypeError(
            "native free-form run text requires a guard-produced SafeText "
            "projection, not an unguarded str"
        )
    return handle.write_text(name, value.text)


_ABSENT = object()


def _lstat_or_absent(path: Path) -> Any:
    """The observed path object, ``_ABSENT`` for a clean ``ENOENT``, else ``None``.

    A clean no-such-path is the **only** route to absent. Any other error is an
    indeterminate observation and must never become a second chance to be
    absent.
    """
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return _ABSENT
    except OSError:
        return None


def _read_observed_regular_file(
    path: Path, observed: Any, *, max_bytes: int
) -> bytes | None:
    """Read exactly the object ``lstat`` observed, or fail closed with ``None``.

    The controls, in order, and why each exists:

    * open ``O_RDONLY|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK`` — the open can neither
      follow a symlink nor block on a writerless FIFO, so a poisoned path
      cannot hang startup;
    * ``fstat`` the **descriptor** and require it to be the same object
      ``lstat`` observed (device + inode) — a regular file swapped for another
      regular file between observation and open is a different object, not a
      shorter version of this one;
    * require a regular file within the byte bound;
    * read from that descriptor and require the byte count to equal the size
      observed **for that descriptor** — a successful short read is a race,
      not a valid smaller document;
    * ``fstat`` once more and require identity, size, and both timestamps to be
      unchanged — a mutation detectable during the read fails closed;
    * an indeterminate close discards the bytes.

    There is no retry: a failed observation is final for this pass.
    """
    if observed is None:
        return None
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        # Includes the unlink-after-lstat race: observed presence is final.
        return None

    raw: bytes | None = None
    try:
        try:
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino) != (observed.st_dev, observed.st_ino):
                raw = None
            elif not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
                raw = None
            else:
                raw = _read_fd_capped(fd, max_bytes + 1)
                if len(raw) != st.st_size:
                    raw = None
                else:
                    after = os.fstat(fd)
                    if (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    ) != (
                        st.st_dev,
                        st.st_ino,
                        st.st_size,
                        st.st_mtime_ns,
                        st.st_ctime_ns,
                    ):
                        raw = None
        except OSError:
            raw = None
    finally:
        try:
            os.close(fd)
        except OSError:
            raw = None
    return raw


def classify_json_document(
    path: Path, *, max_bytes: int = MAX_RECONCILE_JSON_BYTES
) -> JsonDocumentState:
    """Classify one durable JSON artifact as VALID / ABSENT / CORRUPT.

    ``lstat`` distinguishes a clean absence *first*; every other outcome is
    ``CORRUPT``. See :func:`_read_observed_regular_file` for the descriptor
    identity and exact-length controls that make a successful short read, a
    regular-file replacement, and a mutation during the read all fail closed.
    """
    path = Path(path)
    observed = _lstat_or_absent(path)
    if observed is _ABSENT:
        return JsonDocumentState(JsonDocumentKind.ABSENT)
    raw = _read_observed_regular_file(path, observed, max_bytes=max_bytes)

    if raw is None:
        return JsonDocumentState(JsonDocumentKind.CORRUPT)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return JsonDocumentState(JsonDocumentKind.CORRUPT)
    if not isinstance(payload, dict):
        return JsonDocumentState(JsonDocumentKind.CORRUPT)
    return JsonDocumentState(JsonDocumentKind.VALID, payload=payload)


def read_native_terminal_result(path: Path, *, run_id: str) -> NativeTerminalState:
    """Single trusted Native terminal reader for live paths and reconciliation.

    Reads through the same bounded, identity-bound descriptor control as every
    other classified artifact (:func:`_read_observed_regular_file`), then
    delegates schema trust to :func:`validate_native_terminal_result`. Returns
    ABSENT only for a clean no-such-path, TRUSTED(payload), or INVALID
    (corrupt/oversize/symlink/short read/replaced/wrong-schema/compat-only/
    uncertain IO) without raw errors or data.
    """
    path = Path(path)
    observed = _lstat_or_absent(path)
    if observed is _ABSENT:
        return NativeTerminalState(NativeTerminalKind.ABSENT)
    raw = _read_observed_regular_file(
        path, observed, max_bytes=_MAX_TERMINAL_READ_BYTES
    )

    if raw is None:
        return NativeTerminalState(NativeTerminalKind.INVALID)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return NativeTerminalState(NativeTerminalKind.INVALID)

    validated = validate_native_terminal_result(payload, run_id=run_id)
    if validated is None:
        return NativeTerminalState(NativeTerminalKind.INVALID)
    return NativeTerminalState(NativeTerminalKind.TRUSTED, payload=validated)


def _read_fd_capped(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        chunk = os.read(fd, min(65_536, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def create_native_session(
    store: SessionStore,
    *,
    session_id: str,
    profile_id: str,
    profile_revision: int,
    profile_hash: str,
    owner: str,
    namespace: str,
    workspace_hash: str,
    effective_cwd: str,
    matched_root: str | None,
    adapter_contract_hash: str | None = None,
    session_compatibility_epoch: int | None = None,
    agent_id: str | None = None,
    agent_registration_hash: str | None = None,
    now: _dt.datetime | None = None,
) -> SessionRecord:
    """The only sanctioned Native call site for record creation."""
    return store.create_native_session(
        session_id=session_id,
        profile_id=profile_id,
        profile_revision=profile_revision,
        profile_hash=profile_hash,
        owner=owner,
        namespace=namespace,
        workspace_hash=workspace_hash,
        effective_cwd=effective_cwd,
        matched_root=matched_root,
        adapter_contract_hash=adapter_contract_hash,
        session_compatibility_epoch=session_compatibility_epoch,
        agent_id=agent_id,
        agent_registration_hash=agent_registration_hash,
        now=now,
    )


def bind_agent_session(
    store: SessionStore,
    session_id: str,
    *,
    agent_session_id: str,
    now: _dt.datetime | None = None,
) -> SessionRecord:
    """The only sanctioned Native call site for external-ID binding."""
    return store.bind_agent_session(
        session_id, agent_session_id=agent_session_id, now=now
    )

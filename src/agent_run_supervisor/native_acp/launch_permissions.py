"""Profile-selected launch permission material, compiled from the frozen grant.

ACP mediation decides *before* a side effect only when the agent asks. Some
agents do not always ask: Cursor's `agent` mode can complete an edit without
emitting ``session/request_permission``, so ARS's completion backstop sees the
violation only once the file already exists. Detection after the fact is not
prevention.

This module is the earlier line. A **profile** — never an agent id — may select
one closed, source-owned policy id. Before the child is spawned, ARS compiles a
deterministic document from that Run's frozen grant, writes it into a private
per-Run directory under the supervisor root, and hands the child one
source-owned environment pair pointing at it. The agent then refuses the side
effect itself, before ARS ever has to notice one.

Deliberately narrow, and it stays narrow:

* one backend, one document, no dynamic or per-tool approval, no path-level
  write policy, and no positive write/execute grant;
* the supported grant is read-only. A Run whose frozen grant asks for something
  this backend cannot faithfully enforce is refused **before spawn** with a
  stable code rather than being quietly widened or quietly narrowed;
* the ACP :class:`~.permissions.PermissionBridge` and the post-completion
  violation detector are unchanged. This is defense in depth, not a
  replacement for either.

Everything a failure can say is a fixed source literal. Paths, errno text, and
exception detail never reach a caller-visible projection.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

# -- closed policy set ------------------------------------------------------

# Keyed by the capability family it enforces, never by the agent that needs it —
# the same rule the mediation binding ids follow. Which profile selects which
# policy is registry data, and it lives in ``profile.py`` with the rest of it.
POLICY_DENY_WRITE_AND_SHELL_V1 = "deny-write-and-shell-v1"

# The environment key each backend owns, in key **and** value: an operator may
# neither author nor shadow it. Projection applies this layer last, so source
# wins structurally even if the collision check below is ever wrong.
CURSOR_CONFIG_DIR_ENV = "CURSOR_CONFIG_DIR"

# The environment layer this material projects through. Applied last, after
# mediation, and owned here rather than in the spec module so the layer and the
# thing that fills it cannot drift into two spellings.
ENV_SOURCE_LAUNCH_PERMISSION = "launch_permission"

# The private per-Run material, under the Run directory of the supervisor root.
# ARS-owned writable surfaces stay exactly two: this is inside the first one.
LAUNCH_PERMISSION_DIRNAME = "launch-permissions"
LAUNCH_PERMISSION_FILENAME = "cli-config.json"

# The one durable, value-blind fact a failed cleanup leaves behind. An event
# needs an EventWriter and the pre-spawn and emergency paths have none, so the
# classification that has to survive is a marker in the Run directory.
LAUNCH_PERMISSION_CLEANUP_MARKER = "launch-permission-cleanup-failed.json"

# Categorical codes. Each is a fixed literal carrying no input data.
LAUNCH_PERMISSION_UNKNOWN_POLICY = "LAUNCH_PERMISSION_UNKNOWN_POLICY"
LAUNCH_PERMISSION_UNSUPPORTED_GRANT = "LAUNCH_PERMISSION_UNSUPPORTED_GRANT"
LAUNCH_PERMISSION_MATERIALIZE_FAILED = "LAUNCH_PERMISSION_MATERIALIZE_FAILED"
LAUNCH_PERMISSION_CLEANUP_FAILED = "LAUNCH_PERMISSION_CLEANUP_FAILED"

# Capabilities this read-only backend can faithfully enforce. Anything else is
# refused: emitting the same deny-everything document for a write grant would
# silently ignore what the caller froze, and widening the document would grant
# what no evidence supports.
_READ_ONLY_CAPABILITIES = frozenset({"read", "search"})

# The document each policy compiles to. Source-owned in every field.
#
# ``Write(**)`` and ``Shell(*)`` are denied explicitly because leaving either
# unclassified is not safe — an unclassified Write is exactly the case that
# completes without an ACP permission request. Nothing denies a read, so
# ordinary workspace reading is untouched, and no ``allow`` entry exists at all,
# so no positive grant is invented here.
_POLICY_DOCUMENTS: Mapping[str, Mapping[str, Any]] = {
    POLICY_DENY_WRITE_AND_SHELL_V1: {
        "permissions": {"deny": ["Shell(*)", "Write(**)"]},
    },
}

LAUNCH_PERMISSION_POLICY_IDS: frozenset[str] = frozenset(_POLICY_DOCUMENTS)

_POLICY_ENV_NAMES: Mapping[str, str] = {
    POLICY_DENY_WRITE_AND_SHELL_V1: CURSOR_CONFIG_DIR_ENV,
}

RESERVED_LAUNCH_PERMISSION_KEYS: frozenset[str] = frozenset(_POLICY_ENV_NAMES.values())


def _canonical_text(document: Mapping[str, Any]) -> str:
    """Canonical JSON: sorted keys, fixed separators, no incidental whitespace."""
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def reserved_keys_for_policy(policy_id: Any) -> frozenset[str]:
    """The environment keys **this** policy owns, or nothing.

    Per-policy rather than global, so a profile that selects no launch policy is
    not charged for a key it never projects. Total over any input: an
    unhashable or non-string id simply owns nothing.
    """
    if type(policy_id) is not str:
        return frozenset()
    name = _POLICY_ENV_NAMES.get(policy_id)
    return frozenset() if name is None else frozenset({name})

# Bounds on the removal walk, so cleanup can never become unbounded work over a
# tree the child grew. Public because the bound is the contract, and a test that
# cannot name it cannot prove enumeration stops at it.
MAX_CLEANUP_ENTRIES = 4096
MAX_CLEANUP_DEPTH = 16



class LaunchPermissionError(RuntimeError):
    """A launch-permission step failed. Its text is exactly its stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MaterializedLaunchPermissions:
    """What one Run materialized, and the two things the Run path needs.

    ``directory`` is an ephemeral local path. It is handed to the spawn seam
    through :attr:`env_pairs` and is never durable: the launch snapshot records
    the policy id and the content ``digest``, which is what makes the launched
    policy auditable without persisting where it lived or what it said.
    """

    policy_id: str
    digest: str
    directory: str
    env_pairs: tuple[tuple[str, str], ...]


def policy_env_name(policy_id: str) -> str:
    try:
        return _POLICY_ENV_NAMES[policy_id]
    except KeyError:
        raise LaunchPermissionError(LAUNCH_PERMISSION_UNKNOWN_POLICY) from None


def compile_policy_document(
    policy_id: str, *, capabilities: Iterable[str]
) -> str:
    """The canonical document for this policy under this Run's frozen grant.

    Deterministic by construction: canonical JSON with sorted keys and fixed
    separators, so two Runs with the same grant produce byte-identical bytes and
    therefore the same digest.
    """
    document = _POLICY_DOCUMENTS.get(policy_id)
    if document is None:
        raise LaunchPermissionError(LAUNCH_PERMISSION_UNKNOWN_POLICY)
    unsupported = frozenset(capabilities) - _READ_ONLY_CAPABILITIES
    if unsupported:
        raise LaunchPermissionError(LAUNCH_PERMISSION_UNSUPPORTED_GRANT)
    return _canonical_text(document)


def policy_digest(document: str) -> str:
    """The content digest sealed into value-blind launch evidence."""
    return digest_bytes(document.encode("utf-8"))


def canonical_policy_digest(policy_id: Any) -> str | None:
    """The one digest a truthful record for this policy can carry.

    The document is fixed source bytes and capability validation gates the
    compile without editing them, so "a correctly shaped digest" and "the
    policy's digest" are different claims. This table is the second, and every
    seam — materialization, constructor, assembler, reader — asks it rather
    than re-deriving or re-typing a literal.
    """
    if type(policy_id) is not str:
        return None
    return _CANONICAL_POLICY_DIGESTS.get(policy_id)


def digest_bytes(payload: bytes) -> str:
    """The digest of exactly these bytes.

    ``materialize`` digests the buffer it actually wrote, not the document it
    intended to write, so a partial write can never produce material whose
    durable digest describes a document the child will never read.
    """
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def policy_pair_is_exact(policy_id: Any, digest: Any) -> bool:
    """The policy id and its digest are one all-or-none fact.

    Half a pair is not weaker evidence, it is inconsistent evidence: a digest
    with no policy says what was launched under nothing, and a policy with no
    digest names a document nobody can check.

    Total by construction: this answers for any input rather than raising. The
    type is judged **before** any lookup, because an unhashable id reaching
    ``in`` would raise out of a public reader — turning a refusal into a crash
    at whatever called it — and because ``type(x) is str`` rather than
    ``isinstance`` is what closes a subclass that lies about equality or hash.
    That is the rule the free-form storage seams already follow.
    """
    if policy_id is None and digest is None:
        return True
    if policy_id is None or digest is None:
        return False
    if type(policy_id) is not str or type(digest) is not str:
        return False
    # Shape is not identity. The document is fixed source bytes and capability
    # validation gates the compile without editing them, so there is exactly one
    # digest a truthful record can carry — asked of the canonical table rather
    # than re-derived or re-typed anywhere.
    canonical = canonical_policy_digest(policy_id)
    return canonical is not None and digest == canonical


def projection_matches_policy(
    policy_id: Any, pairs: Iterable[tuple[Any, Any]]
) -> bool:
    """The pair and the environment projection describe the same launch.

    ``pairs`` is ``(name, source)`` per projected environment name. Material
    that was compiled but never projected means the child never saw the policy
    while the sealed evidence claims it did; a projected reserved name with no
    pair is the same inconsistency read the other way.

    The check is **name-first**, and that ordering is the point. Filtering only
    by source let the reserved key through under ``base``, ``passthrough``,
    ``overlay``, or ``mediation`` with no pair at all — a record claiming an
    operator supplied a value only source may supply. So a reserved key is
    owned by the launch-permission layer exclusively, in both directions: no
    reserved name under another source, and no other name on that layer.
    """
    entries = tuple(pairs)
    for name, source in entries:
        if type(name) is str and name in RESERVED_LAUNCH_PERMISSION_KEYS:
            if source != ENV_SOURCE_LAUNCH_PERMISSION:
                return False
    projected = [
        name for name, source in entries if source == ENV_SOURCE_LAUNCH_PERMISSION
    ]
    if policy_id is None:
        return not projected
    if type(policy_id) is not str:
        return False
    expected = _POLICY_ENV_NAMES.get(policy_id)
    if expected is None:
        return False
    return projected == [expected]


# Computed once, at import, from the same canonical text every seam uses.
_CANONICAL_POLICY_DIGESTS: Mapping[str, str] = {
    policy_id: digest_bytes(_canonical_text(document).encode("utf-8"))
    for policy_id, document in _POLICY_DOCUMENTS.items()
}


def materialize(
    policy_id: str, *, capabilities: Iterable[str], run_dir: Path
) -> MaterializedLaunchPermissions:
    """Create the private per-Run material, fail-closed.

    The directory is created with :func:`os.mkdir`, which refuses an existing
    path — a directory, a file, or a symlink — rather than adopting it. The
    document is then opened **relative to that directory's own descriptor**
    with ``O_EXCL | O_NOFOLLOW``, so nothing between the check and the write can
    re-point the path.

    Two properties make the refusal honest. Only material whose directory *this
    invocation created* is ever removed on failure, so refusing a pre-existing
    target leaves it exactly as it was found — an exclusive create that deletes
    what it refused is not an exclusive create. And the returned digest is taken
    over the buffer that was fully written, so a short write fails closed
    instead of sealing a digest for bytes that never reached the file.
    """
    document = compile_policy_document(policy_id, capabilities=capabilities)
    env_name = policy_env_name(policy_id)
    payload = document.encode("utf-8")
    directory = Path(run_dir) / LAUNCH_PERMISSION_DIRNAME
    dir_fd: int | None = None
    created = False
    failed = False
    try:
        os.mkdir(directory, 0o700)
        # Set only after mkdir returned: everything this function may later
        # remove is downstream of this line.
        created = True
        dir_fd = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        os.fchmod(dir_fd, 0o700)
        handle = os.open(
            LAUNCH_PERMISSION_FILENAME,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=dir_fd,
        )
        try:
            os.fchmod(handle, 0o600)
            _write_all(handle, payload)
        finally:
            os.close(handle)
        # Closing the directory descriptor is part of the work, not tidying up
        # after it. Left in the ``finally`` below it ran *past* the handler, so
        # a close failure escaped raw — skipping rollback and leaving a
        # directory no later step could classify, because no material carrier
        # was ever returned for the Run to clean up. Clearing ``dir_fd`` first
        # is what makes the guard below unreachable for this descriptor, so it
        # can never be closed twice.
        closing, dir_fd = dir_fd, None
        os.close(closing)
    except OSError:
        failed = True
    finally:
        if dir_fd is not None:
            # Only reachable when the body raised before the explicit close.
            try:
                os.close(dir_fd)
            except OSError:
                # A raise here would escape the handler that already ran.
                failed = True
    if failed:
        # Only what this invocation created, and only if removing it works. A
        # surviving partial is reported as a *cleanup* failure rather than a
        # materialize failure: no ``MaterializedLaunchPermissions`` was returned,
        # so the Run path has nothing to classify later, and this code is the
        # only place the leftover can be named. Both raises are outside the
        # handler, so no cause, context, or traceback retains the errno text or
        # the path it named.
        if created and not _discard_tree(directory, tolerate_failure=False):
            raise LaunchPermissionError(LAUNCH_PERMISSION_CLEANUP_FAILED)
        raise LaunchPermissionError(LAUNCH_PERMISSION_MATERIALIZE_FAILED)
    return MaterializedLaunchPermissions(
        policy_id=policy_id,
        digest=digest_bytes(payload),
        directory=str(directory),
        env_pairs=((env_name, str(directory)),),
    )


def _write_all(handle: int, payload: bytes) -> None:
    """Write every byte, or raise.

    ``os.write`` may legally write fewer bytes than it was handed. Zero or
    otherwise impossible progress is a failure rather than something to retry
    forever: each pass must advance strictly, and cannot advance past the
    buffer.
    """
    total = len(payload)
    view = memoryview(payload)
    written = 0
    while written < total:
        chunk = os.write(handle, view[written:])
        if type(chunk) is not int or chunk <= 0 or written + chunk > total:
            raise OSError(errno.EIO, "short or invalid write progress")
        written += chunk


def discard(material: MaterializedLaunchPermissions) -> None:
    """Remove the material. Idempotent; never follows a symlink out of it.

    The child treats the directory as its own config home and may have written
    state into it, so removal is a bounded walk rather than a single unlink. A
    symlink found inside is unlinked, never descended, so nothing outside the
    directory is ever touched.
    """
    if not _discard_tree(Path(material.directory), tolerate_failure=False):
        raise LaunchPermissionError(LAUNCH_PERMISSION_CLEANUP_FAILED)


def _discard_tree(root: Path, *, tolerate_failure: bool) -> bool:
    try:
        _remove_tree(root, depth=0, budget=[MAX_CLEANUP_ENTRIES])
    except OSError:
        return bool(tolerate_failure)
    except RecursionError:
        return bool(tolerate_failure)
    return True


def _remove_tree(path: Path, *, depth: int, budget: list[int]) -> None:
    if depth > MAX_CLEANUP_DEPTH:
        raise OSError(errno.ELOOP, "cleanup depth bound exceeded")
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat_module.S_ISDIR(info.st_mode):
        # A symlink, a regular file, or anything else: unlink it where it is.
        os.unlink(path)
        return
    # Enumerated **incrementally**, with the budget enforced inside the loop.
    # The child owns what it wrote here, so the listing is not bounded by
    # anything ARS controls: materializing it before checking would let a large
    # tree consume unbounded memory and time inside the synchronous finalizer.
    # Names are collected rather than acted on in place, because unlinking
    # while reading the same directory can skip entries.
    children: list[tuple[str, bool]] = []
    with os.scandir(path) as entries:
        for entry in entries:
            budget[0] -= 1
            if budget[0] < 0:
                raise OSError(errno.ENOSPC, "cleanup entry bound exceeded")
            children.append((entry.path, entry.is_dir(follow_symlinks=False)))
    for child, is_directory in children:
        if is_directory:
            _remove_tree(Path(child), depth=depth + 1, budget=budget)
        else:
            os.unlink(child)
    os.rmdir(path)

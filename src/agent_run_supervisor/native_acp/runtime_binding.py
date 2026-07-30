"""The only reader of an operator-owned Runtime Binding root (PRD R13, C7).

Layer 2 of the three runtime-authority layers. A Binding root is operator
storage outside the repository and outside ``.agent-run-supervisor/``: ARS
opens it read-only and never creates, repairs, or migrates it. The single
exception is the operator command surface, which atomically replaces
``active.json`` and nothing else.

```text
<binding_root>/
└── profiles/<profile_id>/
    ├── active.json                 # regular file, atomically replaced
    └── generations/<generation_id>/
        └── manifest.json           # immutable once written
```

The active-selection namespace is profile-scoped, because one daemon takes one
``--binding-root`` and the registry is closed at several profiles: a root holds
one independently promotable active selection per profile, and no read or write
inside one profile's subtree can observe or alter another's. The component is
derived from the already-resolved closed ``AgentProfile``, never from request
text, and both the pointer and the manifest must declare that same profile as
explicit machine fields.

Everything here is fail-closed and every refusal names its failing rule.
Acceptance rests on the manifest's explicit machine identity fields plus
trusted ownership and artifact validation, and on nothing else: the provenance
block is recorded and reported, never consulted.

Read-once is structural. ``resolve_active`` performs exactly one ``active.json``
read and exactly one generation read; the module-level counters make that
observable to tests, and no other module opens a Binding root.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import selectors
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .agent_registration import AgentRegistration, AgentRegistrationError
from .agent_registration import parse_registration as _parse_registration
from .profile import (
    ARTIFACT_SLOT_KINDS,
    SLOT_DESCRIPTOR_FIELDS,
    SLOT_KIND_CONFIG_ROOT,
    SLOT_KIND_NATIVE_BINARY,
    SLOT_KIND_PACKAGE_TREE,
    AgentProfile,
    VersionProbeRule,
    path_within_root,
)

BINDING_SCHEMA_VERSION = 1

ACTIVE_FILENAME = "active.json"
PROFILES_DIRNAME = "profiles"
AGENTS_DIRNAME = "agents"
GENERATIONS_DIRNAME = "generations"
MANIFEST_FILENAME = "manifest.json"
REGISTRATION_FILENAME = "registration.json"

MAX_BINDING_FILE_BYTES = 65_536
MAX_PACKAGE_TREE_ENTRIES = 20_000
MAX_PACKAGE_TREE_BYTES = 512 * 1024 * 1024
_HASH_BLOCK_BYTES = 1 << 20

_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "generation_id",
        "contract_identity",
        "slots",
        "session_compatibility_epoch",
        "provenance",
    }
)
_POINTER_FIELDS = frozenset(
    {"schema_version", "profile_id", "generation_id", "manifest_sha256"}
)
_CONTRACT_IDENTITY_FIELDS = frozenset(
    {"profile_id", "profile_revision", "adapter_contract_hash"}
)
# Field-set widening is contract-dependent, never global: a profile that is not
# registration-scoped keeps the exact field sets above, so its promoted
# generations and pointers stay byte-identical and keep resolving unchanged.
_AGENT_POINTER_FIELDS = _POINTER_FIELDS | {"agent_id"}
_AGENT_CONTRACT_IDENTITY_FIELDS = _CONTRACT_IDENTITY_FIELDS | {
    "agent_id",
    "agent_registration_hash",
}


def pointer_fields(profile: AgentProfile) -> frozenset[str]:
    """The exact ``active.json`` field set this profile's pointer must carry."""
    if profile.contract.requires_agent_registration:
        return frozenset(_AGENT_POINTER_FIELDS)
    return _POINTER_FIELDS


def contract_identity_fields(profile: AgentProfile) -> frozenset[str]:
    """The exact ``contract_identity`` field set this profile's manifest carries."""
    if profile.contract.requires_agent_registration:
        return frozenset(_AGENT_CONTRACT_IDENTITY_FIELDS)
    return _CONTRACT_IDENTITY_FIELDS
_PROVENANCE_FIELDS = frozenset(
    {"created_at", "accepted_by", "accepted_at", "acceptance_receipt"}
)
_RECEIPT_FIELDS = frozenset({"ref", "sha256"})

# Generation ids name a directory under ``generations/``: safe path components
# only. No dots (rules out ``.``/``..``), no separators, no whitespace.
_GENERATION_ID_MAX = 128


class BindingRefusal(Exception):
    """Fail-closed Binding refusal; ``rule`` names the failing rule.

    The message never carries file bytes, credential material, or provenance
    text — only the rule, the surface, and structural facts.
    """

    def __init__(self, *, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message


# -- read-once instrumentation ----------------------------------------------

_READ_COUNTERS: dict[str, int] = {"registration": 0, "active": 0, "generation": 0}


def read_counters() -> dict[str, int]:
    """Snapshot of Binding reads since the last reset (C8 instrumentation)."""
    return dict(_READ_COUNTERS)


def reset_read_counters() -> None:
    for key in _READ_COUNTERS:
        _READ_COUNTERS[key] = 0


# -- ownership ---------------------------------------------------------------


@dataclass(frozen=True)
class TrustedOwnership:
    """Who may own a Binding root and the artifacts it names (C5).

    ``root`` (uid 0) is always trusted. ``service_uid`` is the ``arsd``/AGENT
    UID: an artifact it owns is an artifact it can rewrite, so same-UID
    ownership is a refusal, not an exemption.
    """

    trusted_uids: frozenset[int]
    service_uid: int

    def __post_init__(self) -> None:
        if not isinstance(self.service_uid, int) or self.service_uid < 0:
            raise ValueError("service_uid must be a non-negative integer")
        if self.service_uid == 0:
            raise ValueError("a root service UID can rewrite every artifact")
        if self.service_uid in self.trusted_uids:
            raise ValueError("service_uid must not be inside the trusted set")

    def is_trusted(self, uid: int) -> bool:
        return uid == 0 or uid in self.trusted_uids


def default_ownership() -> TrustedOwnership:
    """Production default: only root-owned artifact roots are trusted.

    Preparing such a root is an operator installation action outside ARS; this
    module never escalates privilege and never relaxes the rule to make a
    same-UID deployment pass.
    """
    return TrustedOwnership(trusted_uids=frozenset({0}), service_uid=os.geteuid())


def _refuse(rule: str, message: str) -> BindingRefusal:
    return BindingRefusal(rule=rule, message=f"runtime binding refused [{rule}]: {message}")


def check_ownership(
    info: os.stat_result,
    ownership: TrustedOwnership,
    surface: str,
    *,
    traversal_ancestor: bool = False,
) -> None:
    """Trusted owner, and not rewritable by the service/AGENT UID (C5).

    ``traversal_ancestor`` marks the one surface class where a sticky
    world-writable *directory* is admissible, and the default is the strict rule
    because sticky is only ever half of immutability. A sticky directory stops a
    non-owner from renaming or removing the entries inside it, which is exactly
    the guarantee an ambient ancestor like ``/tmp`` has to provide: the walk
    selects one trusted-owned child there and nothing else, so that child cannot
    be swapped out from under a dirfd-relative descent.

    It says nothing whatever about *adding* entries, so it cannot stand in for
    immutability on a protected object — a Binding root, a generation directory,
    a package root, any directory inside a code closure. A new file inside a
    hashed closure is sibling code no digest ever froze, and the service/AGENT UID
    could place it there after the final recheck and before the wrapped adapter
    reopens the tree. Those surfaces therefore take the strict rule, and they take
    it by default so a new call site cannot inherit the exemption by omission.
    """
    if info.st_uid == ownership.service_uid:
        raise _refuse(
            "SERVICE_UID_WRITABLE", f"{surface} is owned by the arsd/AGENT UID"
        )
    if not ownership.is_trusted(info.st_uid):
        raise _refuse("UNTRUSTED_OWNER", f"{surface} is owned outside the trusted set")
    mode = stat.S_IMODE(info.st_mode)
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        sticky_dir = stat.S_ISDIR(info.st_mode) and bool(mode & stat.S_ISVTX)
        if traversal_ancestor and sticky_dir:
            return
        raise _refuse(
            "GROUP_OR_OTHER_WRITABLE",
            f"{surface} is group- or other-writable"
            + (
                " (sticky protects removal, not the entries that may be added)"
                if sticky_dir
                else ""
            ),
        )


def check_ancestors(path: Path, ownership: TrustedOwnership, surface: str) -> None:
    """Every ancestor of an artifact must be trusted and non-rewritable.

    Walked with ``lstat`` so a symlinked ancestor is refused rather than
    silently resolved onto a trusted target. Everything this walks is strictly
    *above* the artifact, so each step is a traversal ancestor: what has to hold
    is that the named child cannot be replaced, which is what sticky provides.
    The artifact itself, and any directory whose contents are part of a protected
    object, are checked by their own caller under the strict rule.
    """
    current = path.parent
    while True:
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise _refuse(
                "ANCESTOR_UNREADABLE", f"{surface} ancestor cannot be inspected: {exc.errno}"
            ) from None
        if stat.S_ISLNK(info.st_mode):
            raise _refuse("SYMLINKED_ANCESTOR", f"{surface} has a symlinked ancestor")
        if not stat.S_ISDIR(info.st_mode):
            raise _refuse("NOT_A_DIRECTORY", f"{surface} ancestor is not a directory")
        check_ownership(info, ownership, f"{surface} ancestor", traversal_ancestor=True)
        parent = current.parent
        if parent == current:
            return
        current = parent


def _require_safe_absolute(value: Any, surface: str) -> Path:
    if not isinstance(value, str) or not value:
        raise _refuse("ARTIFACT_PATH_UNSAFE", f"{surface} must be a non-empty string")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise _refuse(
            "ARTIFACT_PATH_UNSAFE", f"{surface} must be an absolute path without '..'"
        )
    return path


# -- low-level fail-closed reads ---------------------------------------------


def _open_dir(
    name: str | Path,
    *,
    dir_fd: int | None = None,
    surface: str,
    symlink_rule: str = "NOT_A_DIRECTORY",
    missing_rule: str | None = None,
) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        return os.open(str(name), flags, dir_fd=dir_fd)
    except NotADirectoryError:
        # ``O_DIRECTORY`` reports ENOTDIR for a symlink before ``O_NOFOLLOW``
        # can report ELOOP, so the entry is re-examined to name the right rule.
        # The refusal is already decided; only its label depends on this.
        if _is_symlink_entry(name, dir_fd=dir_fd):
            raise _refuse(symlink_rule, f"{surface} is a symlink") from None
        raise _refuse("NOT_A_DIRECTORY", f"{surface} is not a directory") from None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, 62):  # ELOOP variants on O_NOFOLLOW
            raise _refuse(symlink_rule, f"{surface} is a symlink") from None
        # "absent" and "unopenable" are different operator facts, and only the
        # caller knows whether absence deserves its own rule here.
        if missing_rule is not None and exc.errno == errno.ENOENT:
            raise _refuse(missing_rule, f"{surface} does not exist") from None
        raise _refuse("OPEN_FAILED", f"{surface} could not be opened") from None


def _is_symlink_entry(name: str | Path, *, dir_fd: int | None) -> bool:
    try:
        info = os.lstat(str(name), dir_fd=dir_fd)
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode)


def open_trusted_dir(path: Path | str, *, ownership: TrustedOwnership, surface: str) -> int:
    """Open a directory by walking it from ``/``, proving every ancestor (C7).

    ``O_NOFOLLOW`` on the final component proves only that the leaf is not a
    symlink; an untrusted ancestor still redirects or exposes everything
    beneath it. The walk is dirfd-relative throughout, so the chain that was
    verified is the chain the returned descriptor belongs to — nothing is
    re-resolved by pathname afterwards, and there is no reopen to race.

    The caller owns closing the returned descriptor. Only the components strictly
    above the target are traversal ancestors; the target itself is the protected
    object this descriptor will be used to read, so it takes the strict rule.
    """
    target = Path(path)
    if not target.is_absolute() or ".." in target.parts:
        raise _refuse(
            "ARTIFACT_PATH_UNSAFE", f"{surface} must be an absolute path without '..'"
        )
    parts = target.parts[1:]
    fd = _open_dir("/", surface=f"{surface} ancestor")
    try:
        check_ownership(
            os.fstat(fd), ownership, f"{surface} ancestor", traversal_ancestor=True
        )
        for index, component in enumerate(parts):
            last = index == len(parts) - 1
            child = _open_dir(
                component,
                dir_fd=fd,
                surface=surface if last else f"{surface} ancestor",
                symlink_rule="NOT_A_DIRECTORY" if last else "SYMLINKED_ANCESTOR",
            )
            os.close(fd)
            fd = child
            check_ownership(
                os.fstat(fd),
                ownership,
                surface if last else f"{surface} ancestor",
                traversal_ancestor=not last,
            )
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_profile_dir(
    root_fd: int, profile: AgentProfile, *, ownership: TrustedOwnership
) -> int:
    """Descend a verified root to ``profiles/<profile_id>/``.

    The component comes from the resolved closed profile object — the registry's
    own constant. Each step is dirfd-relative and ``O_NOFOLLOW``, so the subtree
    that was proven is the subtree the returned descriptor belongs to.

    One level deeper, an agent-scoped profile descends again through
    :func:`_open_agent_dir`, and *that* component does come from request text.
    It is proven safe by :func:`agent_component` before any filesystem call, so
    the value that reaches a path here is judged text rather than trusted text.

    Both directories are protected objects rather than ambient traversal
    ancestors: what they contain *is* one profile's activation state, so they
    take the strict ownership rule. The caller owns closing the descriptor.
    """
    component = profile_component(profile.profile_id)
    fd = root_fd
    opened: int | None = None
    try:
        for name, surface in (
            (PROFILES_DIRNAME, f"{PROFILES_DIRNAME}/"),
            (component, f"{PROFILES_DIRNAME}/{component}/"),
        ):
            try:
                child = _open_dir(
                    name,
                    dir_fd=fd,
                    surface=surface,
                    missing_rule="PROFILE_BINDING_ABSENT",
                )
            except BindingRefusal as refusal:
                if refusal.rule != "PROFILE_BINDING_ABSENT":
                    raise
                raise _absent_or_legacy(root_fd, surface) from None
            if opened is not None:
                os.close(opened)
            opened = fd = child
            check_ownership(os.fstat(fd), ownership, surface)
        assert opened is not None  # the loop opens at least one descriptor
        return opened
    except BaseException:
        if opened is not None:
            os.close(opened)
        raise


def _open_agent_dir(
    profile_fd: int, agent_id: str, *, ownership: TrustedOwnership
) -> int:
    """Descend a verified profile subtree to ``agents/<agent_id>/``.

    ``agent_id`` is the first caller-supplied value in this repository ever to
    become a path component, so the order here is the whole safety argument:
    :func:`agent_component` has already judged and frozen it *before* this
    function is reached and before any filesystem call is made. Beyond that,
    the descent is dirfd-relative and ``O_NOFOLLOW`` under an
    ownership-verified directory, ARS creates nothing, and the registration
    inside re-declares the same ``agent_id`` as an explicit machine field — so
    a caller can only ever name a directory an operator authored under a
    trusted root, and naming it is not the same as being believed by it.
    """
    component = agent_component(agent_id)
    fd = profile_fd
    opened: int | None = None
    try:
        for name, surface, missing in (
            (AGENTS_DIRNAME, f"{AGENTS_DIRNAME}/", "PROFILE_BINDING_ABSENT"),
            (component, f"{AGENTS_DIRNAME}/{component}/", "AGENT_BINDING_ABSENT"),
        ):
            child = _open_dir(name, dir_fd=fd, surface=surface, missing_rule=missing)
            if opened is not None:
                os.close(opened)
            opened = fd = child
            check_ownership(os.fstat(fd), ownership, surface)
        assert opened is not None  # the loop opens at least one descriptor
        return opened
    except BaseException:
        if opened is not None:
            os.close(opened)
        raise


def _open_anchor_dir(
    root: Path | str,
    profile: AgentProfile,
    agent_id: str | None,
    *,
    ownership: TrustedOwnership,
) -> int:
    """The one directory this profile's (and agent's) Binding material lives in.

    Read and write share this descent, so a promotion can never reach a
    directory a resolution would not, or vice versa. For the three live
    profiles it is exactly the descent they have always taken; the agent anchor
    is a new subtree only an agent-scoped profile ever enters. The caller owns
    closing the returned descriptor.
    """
    root_fd = open_trusted_dir(root, ownership=ownership, surface="binding root")
    try:
        check_ownership(os.fstat(root_fd), ownership, "binding root")
        profile_fd = _open_profile_dir(root_fd, profile, ownership=ownership)
    finally:
        os.close(root_fd)
    if agent_id is None:
        return profile_fd
    try:
        return _open_agent_dir(profile_fd, agent_id, ownership=ownership)
    finally:
        os.close(profile_fd)


def _absent_or_legacy(root_fd: int, surface: str) -> BindingRefusal:
    """Name a pre-0.5.2 root instead of reporting a bare missing directory.

    The old layout put one ``active.json`` at the root, so one configured root
    could activate exactly one profile — every other registered profile failed
    its contract-identity check against whichever generation happened to be
    promoted. It is refused rather than read: its pointer carries no profile
    identity, so it cannot even say which profile it activates, and honouring it
    would reinstate that defect silently for two profiles out of three.

    ARS never writes, repairs, or migrates operator storage, so the refusal
    names the operator action instead of performing one.
    """
    try:
        legacy = os.lstat(ACTIVE_FILENAME, dir_fd=root_fd)
    except OSError:
        legacy = None
    if legacy is not None and stat.S_ISREG(legacy.st_mode):
        return _refuse(
            "LEGACY_BINDING_LAYOUT",
            "binding root carries a pre-0.5.2 root-level active.json; move each "
            f"generation under {PROFILES_DIRNAME}/<profile_id>/{GENERATIONS_DIRNAME}/ "
            "and re-promote every profile",
        )
    return _refuse(
        "PROFILE_BINDING_ABSENT",
        f"binding root has no {surface} for this profile",
    )


def _read_regular_file(
    dir_fd: int, name: str, *, surface: str, missing_rule: str | None = None
) -> bytes:
    try:
        info = os.lstat(name, dir_fd=dir_fd)
    except OSError as exc:
        # "absent" and "unopenable" are different operator facts; only the
        # caller knows whether absence deserves a rule of its own here.
        if missing_rule is not None and exc.errno == errno.ENOENT:
            raise _refuse(missing_rule, f"{surface} does not exist") from None
        raise _refuse("OPEN_FAILED", f"{surface} could not be inspected") from None
    if not stat.S_ISREG(info.st_mode):
        raise _refuse("NOT_A_REGULAR_FILE", f"{surface} is not a regular file")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except OSError:
        raise _refuse("OPEN_FAILED", f"{surface} could not be opened") from None
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise _refuse("NOT_A_REGULAR_FILE", f"{surface} is not a regular file")
        if opened.st_size > MAX_BINDING_FILE_BYTES:
            raise _refuse("FILE_TOO_LARGE", f"{surface} exceeds the size bound")
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_BINDING_FILE_BYTES:
            chunk = os.read(fd, min(65_536, MAX_BINDING_FILE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_BINDING_FILE_BYTES:
            raise _refuse("FILE_TOO_LARGE", f"{surface} exceeds the size bound")
    finally:
        os.close(fd)
    return b"".join(chunks)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _decode_canonical(raw: bytes, *, surface: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _refuse("NON_CANONICAL_JSON", f"{surface} is not UTF-8") from None
    try:
        payload = json.loads(text)
    except ValueError:
        raise _refuse("NON_CANONICAL_JSON", f"{surface} is not valid JSON") from None
    if not isinstance(payload, dict):
        raise _refuse("NON_CANONICAL_JSON", f"{surface} is not a JSON object")
    if _canonical_json(payload) != text:
        raise _refuse(
            "NON_CANONICAL_JSON", f"{surface} is not canonical JSON byte for byte"
        )
    return payload


def _require_fields(
    payload: Mapping[str, Any], allowed: Iterable[str], *, rule: str, surface: str
) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise _refuse(rule, f"{surface} carries unknown field(s): {unknown}")


# A profile id names a directory under ``profiles/``. Registry ids are code
# constants rather than caller input, so this is defence in depth rather than
# input filtering — but a future registration must not be able to introduce
# traversal by being registered, so the component is proven safe on every use.
# Dots are admissible because registered ids carry versions (``codex-acp-1.1.7``);
# ``.``, ``..``, and any ``..`` run are not.
_PROFILE_COMPONENT_MAX = 128


def profile_component(profile_id: Any) -> str:
    """The one directory component a profile's Binding material may live under."""
    if (
        not isinstance(profile_id, str)
        or not profile_id
        or len(profile_id) > _PROFILE_COMPONENT_MAX
    ):
        raise _refuse("PROFILE_ID_UNSAFE", "profile id is missing or oversized")
    if not profile_id[0].isalnum():
        raise _refuse("PROFILE_ID_UNSAFE", "profile id must start alphanumeric")
    if ".." in profile_id:
        raise _refuse("PROFILE_ID_UNSAFE", "profile id carries a traversal run")
    for char in profile_id[1:]:
        if not (char.isalnum() or char in "_-."):
            raise _refuse("PROFILE_ID_UNSAFE", "profile id has an unsafe character")
    return profile_id


# An agent id names a directory under ``agents/``. Unlike a profile id it is
# *caller text*, so this is input filtering rather than defence in depth, and it
# is deliberately narrower: no dots at all, so ``.``, ``..``, and every
# traversal run are excluded by the character set rather than by a special case.
_AGENT_COMPONENT_MAX = 64


def agent_component(agent_id: Any) -> str:
    """The one directory component an agent's Binding material may live under.

    Called before any filesystem query on every path that descends into an
    agent subtree — the ordering is the safety property, not the grammar alone.

    The type is judged by ``type(v) is str`` rather than ``isinstance``, and the
    judged value is returned rather than the argument. A ``str`` subclass can
    override ``__str__``, ``__eq__``, and ``__class__`` and so pass a check on
    one value while presenting another to the reader that actually opens the
    path; ``isinstance`` admits exactly that subclass, and ``type(v) in (...)``
    still consults a metaclass ``__eq__``. This repository has already paid for
    that bug once in :mod:`agent_run_supervisor.arsd.operand`, and the fix there
    is the fix here.
    """
    if type(agent_id) is not str:
        raise _refuse("AGENT_ID_UNSAFE", "agent id must be an exact string")
    # Frozen once: everything below judges — and everything above returns — this
    # value, never the caller's object.
    value = str.__str__(agent_id)
    if type(value) is not str:  # pragma: no cover - str.__str__ is not overridable here
        raise _refuse("AGENT_ID_UNSAFE", "agent id did not freeze to an exact string")
    if not value or len(value) > _AGENT_COMPONENT_MAX:
        raise _refuse("AGENT_ID_UNSAFE", "agent id is missing or oversized")
    if not value.isascii():
        raise _refuse("AGENT_ID_UNSAFE", "agent id is not ASCII")
    if not (value[0].isalnum() and value[0].isascii()):
        raise _refuse("AGENT_ID_UNSAFE", "agent id must start alphanumeric")
    for char in value[1:]:
        if not (char.isalnum() or char in "_-"):
            raise _refuse("AGENT_ID_UNSAFE", "agent id has an unsafe character")
    return value


def profile_binding_dir(root: Path | str, profile_id: str) -> Path:
    """``<root>/profiles/<profile_id>`` — lexical only, and never created here."""
    return Path(root) / PROFILES_DIRNAME / profile_component(profile_id)


def agent_binding_dir(root: Path | str, profile_id: str, agent_id: str) -> Path:
    """``<root>/profiles/<profile_id>/agents/<agent_id>`` — lexical only.

    A new subtree that only an agent-scoped profile ever descends into; the
    three live profiles' descent is untouched.
    """
    return (
        profile_binding_dir(root, profile_id)
        / AGENTS_DIRNAME
        / agent_component(agent_id)
    )


def _binding_dir(root: Path | str, profile_id: str, agent_id: str | None) -> Path:
    if agent_id is None:
        return profile_binding_dir(root, profile_id)
    return agent_binding_dir(root, profile_id, agent_id)


def active_pointer_path(
    root: Path | str, profile_id: str, *, agent_id: str | None = None
) -> Path:
    """The one file a promotion for this profile (and agent) replaces."""
    return _binding_dir(root, profile_id, agent_id) / ACTIVE_FILENAME


def registration_path(root: Path | str, profile_id: str, agent_id: str) -> Path:
    """Where one agent's operator-authored registration lives."""
    return agent_binding_dir(root, profile_id, agent_id) / REGISTRATION_FILENAME


def generation_manifest_path(
    root: Path | str,
    profile_id: str,
    generation_id: str,
    *,
    agent_id: str | None = None,
) -> Path:
    """Where one generation manifest lives; operator-authored."""
    return (
        _binding_dir(root, profile_id, agent_id)
        / GENERATIONS_DIRNAME
        / _safe_generation_id(generation_id)
        / MANIFEST_FILENAME
    )


def _safe_generation_id(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > _GENERATION_ID_MAX:
        raise _refuse("GENERATION_ID_UNSAFE", "generation_id is missing or oversized")
    if not value[0].isalnum():
        raise _refuse("GENERATION_ID_UNSAFE", "generation_id must start alphanumeric")
    for char in value[1:]:
        if not (char.isalnum() or char in "_-"):
            raise _refuse(
                "GENERATION_ID_UNSAFE", "generation_id has an unsafe character"
            )
    return value


# -- package closure digest --------------------------------------------------


def package_tree_digest(
    root: Path,
    *,
    max_entries: int = MAX_PACKAGE_TREE_ENTRIES,
    max_bytes: int = MAX_PACKAGE_TREE_BYTES,
    ownership: TrustedOwnership | None = None,
) -> str:
    """Canonical digest over a package root's complete code closure (C5).

    Deterministic traversal, length-prefixed so a name boundary can never be
    shifted into a neighbour, and bounded: an oversized tree is refused rather
    than sampled. Directories and regular files are absorbed by name and
    content; a symlink is absorbed by its target *text* once every point the
    kernel would walk to reach that target is proven to stay inside the root
    (see :func:`_prove_target_within_root`), and a special file is refused
    outright — an immutable package root contains nothing a runtime could read
    from outside the closure.

    With ``ownership``, every entry is ownership- and mode-checked as it is
    walked. A digest alone would freeze the sibling code only until someone who
    can write it changes it; the point of the closure is that nobody outside
    the trusted set can.
    """
    root = Path(root)
    digest = hashlib.sha256()
    entries = 0
    total_bytes = 0
    stack: list[tuple[Path, str]] = [(root, "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            raise _refuse(
                "PACKAGE_TREE_UNREADABLE", "package root entry could not be listed"
            ) from None
        for name in reversed(names):
            child = directory / name
            relative = f"{prefix}{name}"
            info = os.lstat(child)
            if stat.S_ISLNK(info.st_mode):
                entries += 1
                total_bytes += info.st_size
                if total_bytes > max_bytes:
                    raise _refuse(
                        "PACKAGE_TREE_TOO_LARGE", "package closure exceeds the byte bound"
                    )
                if ownership is not None:
                    _check_symlink_owner(ownership, info)
                _absorb(digest, relative, "l", _contained_link_target(root, child))
            else:
                if ownership is not None:
                    check_ownership(info, ownership, "package closure entry")
                if stat.S_ISDIR(info.st_mode):
                    stack.append((child, f"{relative}/"))
                    entries += 1
                    _absorb(digest, relative, "d", b"")
                elif stat.S_ISREG(info.st_mode):
                    entries += 1
                    total_bytes += info.st_size
                    if total_bytes > max_bytes:
                        raise _refuse(
                            "PACKAGE_TREE_TOO_LARGE",
                            "package closure exceeds the byte bound",
                        )
                    _absorb(digest, relative, "f", _file_digest(child))
                else:
                    raise _refuse(
                        "PACKAGE_TREE_UNSAFE_ENTRY",
                        "package closure contains a special file",
                    )
            if entries > max_entries:
                raise _refuse(
                    "PACKAGE_TREE_TOO_LARGE", "package closure exceeds the entry bound"
                )
    return digest.hexdigest()


def _check_symlink_owner(ownership: TrustedOwnership, info: os.stat_result) -> None:
    """Owner rules only: a symlink's permission bits carry no authority.

    Linux ignores a symlink's mode, so the strict group/other-write rule has
    nothing to judge on one. What actually stops a link being retargeted is the
    strict rule already applied to the directory holding it, and what this adds
    is that the service/AGENT UID may not own the link itself.
    """
    if info.st_uid == ownership.service_uid:
        raise _refuse(
            "SERVICE_UID_WRITABLE", "package closure symlink is owned by the arsd/AGENT UID"
        )
    if not ownership.is_trusted(info.st_uid):
        raise _refuse(
            "UNTRUSTED_OWNER", "package closure symlink is owned outside the trusted set"
        )


# A target is walked the way the kernel walks it, under two bounds: hops, at
# the kernel's own ``MAXSYMLINKS`` order, and total components, so that one
# expansion cannot amplify into an unbounded walk. Exceeding either is
# undecidable, not proven safe.
_SYMLINK_HOP_MAX = 40
_SYMLINK_STEP_MAX = 4096


def _contained_link_target(root: Path, link: Path) -> bytes:
    """The link's own text, once it is proven not to leave the closure.

    A real package install is full of symlinks — ``node_modules/.bin`` alone
    guarantees them — so refusing every one would leave the closure unable to
    model the tree it exists to freeze. Absorbing the target *text* freezes the
    name without ever following it; what must still be refused is a link that
    resolves outside the root, because that is code no tree digest covers.

    Containment is a gate, never a digest input: the bytes absorbed are the raw
    text, so what the proof below decides is which trees are *accepted*, and an
    honest tree's digest is unaffected by it.
    """
    raw = os.readlink(link)
    _prove_target_within_root(root, link.parent, raw)
    return raw.encode("utf-8", "surrogateescape")


def _symlink_escape() -> BindingRefusal:
    """One wording for one fact: a link names code outside the closure."""
    return _refuse(
        "PACKAGE_TREE_SYMLINK_ESCAPE",
        "package closure contains a symlink resolving outside the root",
    )


def _prove_target_within_root(root: Path, here: Path, raw: str) -> None:
    """Walk a link's target as the kernel would, refusing any point outside.

    A lexical walk cannot decide this and is not a conservative approximation
    of the kernel's. ``normpath`` cancels ``X/..`` as text, while the kernel
    applies ``..`` to whatever ``X`` *resolved to*, so a target composed of two
    individually contained links can still land outside: with ``a/b/up -> ".."``
    the target ``a/b/up/../../x`` reads as ``<root>/a/x`` as text and resolves
    to ``<root>/../x``. For the same reason no hop is safe by induction —
    containment of a link target is not closed under composition with ``..``.

    So every point the kernel would walk is judged, one component at a time. An
    intermediate point may *be* the root — a real ``.bin`` link reaching
    ``../../lib/x.js`` passes through it — while the target itself must be
    strictly inside, a root not being its own member. Each point is judged
    before it is read, so no filesystem call is ever issued on a path outside
    the root, and no component above the root is ever resolved.

    Two outcomes are undecidable rather than escaping — a cycle, and a walk
    past the bounds above — and both are refused rather than accepted or
    chased. A dangling in-root name is neither: it reaches nothing, and the
    trusted write inside the root that would later create it changes this very
    digest, so it stays covered and is frozen by its text.

    Preconditions, both held elsewhere: the traversal descends only real
    directories, so ``here`` carries no unexpanded link; and that the root's own
    ancestors are not links is proven at the Binding layer by
    ``check_ancestors`` (``SYMLINKED_ANCESTOR``). Pinning the walked points
    against a concurrent rewrite is not this layer's job either — the closure
    proves what the tree *is*, and the attestation layer owns the recheck.
    """
    current, pending = _link_walk_start(root, here, raw)
    hops = steps = 0
    while pending:
        steps += 1
        if steps > _SYMLINK_STEP_MAX:
            raise _refuse(
                "PACKAGE_TREE_SYMLINK_UNRESOLVABLE",
                "package closure contains a symlink exceeding the walk bound",
            )
        name = pending.pop(0)
        if name in ("", "."):
            continue
        candidate = current.parent if name == ".." else current / name
        # Judged before it is read, and the root itself passes only while
        # components remain: a point walked *through* may be the root, the
        # point walked *to* may not.
        if not path_within_root(root, candidate) and not (pending and candidate == root):
            raise _symlink_escape()
        if name == "..":
            current = candidate
            continue
        try:
            text = os.readlink(candidate)
        except OSError as error:
            if error.errno == errno.EINVAL:  # a real component, not a link
                current = candidate
                continue
            if error.errno == errno.ENOENT:  # in the root and absent: reaches nothing
                return
            raise _refuse(
                "PACKAGE_TREE_SYMLINK_UNRESOLVABLE",
                "package closure contains a symlink that could not be resolved",
            ) from None
        hops += 1
        if hops > _SYMLINK_HOP_MAX:
            raise _refuse(
                "PACKAGE_TREE_SYMLINK_UNRESOLVABLE",
                "package closure contains a symlink cycle or an over-deep chain",
            )
        current, expanded = _link_walk_start(root, candidate.parent, text)
        pending = expanded + pending
    if not path_within_root(root, current):
        raise _symlink_escape()


def _link_walk_start(root: Path, here: Path, text: str) -> tuple[Path, list[str]]:
    """Where a target's walk starts, and the components left to walk.

    An absolute target restarts the walk at the root, so its leading components
    must be the root's own — compared as text, with no ``..`` elided, because
    eliding is the very confusion this walk exists to avoid. What follows the
    root is then walked like any other component, which is what keeps an
    in-root absolute target legible while still refusing one that jumps out.
    """
    if not text.startswith("/"):
        return here, text.split("/")
    parts, root_parts = Path(text).parts, root.parts
    if parts[: len(root_parts)] != root_parts:
        raise _symlink_escape()
    return root, list(parts[len(root_parts) :])


def _absorb(digest: "hashlib._Hash", relative: str, kind: str, payload: bytes) -> None:
    encoded = relative.encode("utf-8")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    digest.update(kind.encode("ascii"))
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def _file_digest(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        digest = hashlib.sha256()
        while True:
            block = os.read(fd, _HASH_BLOCK_BYTES)
            if not block:
                break
            digest.update(block)
        return digest.digest()
    finally:
        os.close(fd)


# -- the kernel-selected interpreter of an executable image (C5/C10) ---------

_SHEBANG_MAX_BYTES = 512
_ELF_MAX_PHNUM = 4096
_ELF_MAX_INTERP_BYTES = 4096
# ``env`` resolves its argument through PATH, so a shebang naming it freezes
# nothing. It is the one launcher form that is refused by name rather than by
# comparison, because there is no interpreter identity to compare against.
_PATH_RESOLVING_LAUNCHERS = frozenset({"env"})


def required_interpreter(path: Path, *, surface: str) -> str | None:
    """The interpreter the kernel will select for ``path``, or ``None``.

    A file digest freezes an executable's own bytes and nothing else. A script
    is run by whatever its shebang names; a dynamically linked ELF is run by
    the loader in its ``PT_INTERP`` segment. Either way the code that actually
    executes lives partly outside the hashed file, so C5's "interpreter or
    dynamic-loader policy where one applies" needs the real answer, read from
    the image rather than declared by the operator.

    ``None`` means the image needs no external interpreter — a static ELF the
    kernel maps directly. The interpreter's *own* loader chain below this one
    level is the platform's trusted base and is not walked; what this module
    guarantees is that the immediate runtime is frozen and trusted, not that
    the whole OS is.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise _refuse("ARTIFACT_UNREADABLE", f"{surface} could not be opened") from None
    try:
        head = os.pread(fd, 64, 0)
        if head[:2] == b"#!":
            return _shebang_interpreter(head, fd, surface=surface)
        if head[:4] == b"\x7fELF":
            return _elf_interpreter(head, fd, surface=surface)
    except OSError:
        raise _refuse("ARTIFACT_UNREADABLE", f"{surface} could not be read") from None
    finally:
        os.close(fd)
    raise _refuse(
        "UNKNOWN_EXECUTABLE_FORMAT",
        f"{surface} is neither a shebang script nor an ELF image",
    )


def _shebang_interpreter(head: bytes, fd: int, *, surface: str) -> str:
    line = os.pread(fd, _SHEBANG_MAX_BYTES, 0).split(b"\n", 1)[0]
    if len(line) >= _SHEBANG_MAX_BYTES:
        raise _refuse("INTERPRETER_UNRESOLVABLE", f"{surface} shebang is oversized")
    try:
        text = line[2:].decode("utf-8").strip()
    except UnicodeDecodeError:
        raise _refuse("INTERPRETER_UNRESOLVABLE", f"{surface} shebang is not UTF-8") from None
    token = text.split()[0] if text.split() else ""
    if not token:
        raise _refuse("INTERPRETER_UNRESOLVABLE", f"{surface} shebang names nothing")
    candidate = Path(token)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise _refuse(
            "INTERPRETER_INDIRECT", f"{surface} shebang is not an absolute path"
        )
    if candidate.name in _PATH_RESOLVING_LAUNCHERS:
        raise _refuse(
            "INTERPRETER_INDIRECT", f"{surface} resolves its runtime through PATH"
        )
    return str(candidate)


def _elf_interpreter(head: bytes, fd: int, *, surface: str) -> str | None:
    bits, endian = head[4], head[5]
    if bits not in (1, 2) or endian not in (1, 2):
        raise _refuse("UNKNOWN_EXECUTABLE_FORMAT", f"{surface} has an unreadable ELF class")
    prefix = "<" if endian == 1 else ">"
    if bits == 2:  # ELF64
        phoff, phentsize, phnum = (
            struct.unpack_from(f"{prefix}Q", head, 32)[0],
            struct.unpack_from(f"{prefix}H", head, 54)[0],
            struct.unpack_from(f"{prefix}H", head, 56)[0],
        )
        entry_fmt, type_off, off_off, size_off = f"{prefix}I", 0, 8, 32
    else:  # ELF32
        phoff, phentsize, phnum = (
            struct.unpack_from(f"{prefix}I", head, 28)[0],
            struct.unpack_from(f"{prefix}H", head, 42)[0],
            struct.unpack_from(f"{prefix}H", head, 44)[0],
        )
        entry_fmt, type_off, off_off, size_off = f"{prefix}I", 0, 4, 16
    if phnum > _ELF_MAX_PHNUM or phentsize < size_off + 8:
        raise _refuse(
            "UNKNOWN_EXECUTABLE_FORMAT", f"{surface} has an out-of-range program header"
        )
    for index in range(phnum):
        entry = os.pread(fd, phentsize, phoff + index * phentsize)
        if len(entry) < phentsize:
            raise _refuse(
                "UNKNOWN_EXECUTABLE_FORMAT", f"{surface} program header is truncated"
            )
        if struct.unpack_from(entry_fmt, entry, type_off)[0] != 3:  # PT_INTERP
            continue
        width = "Q" if bits == 2 else "I"
        offset = struct.unpack_from(f"{prefix}{width}", entry, off_off)[0]
        length = struct.unpack_from(f"{prefix}{width}", entry, size_off)[0]
        if not 0 < length <= _ELF_MAX_INTERP_BYTES:
            raise _refuse(
                "INTERPRETER_UNRESOLVABLE", f"{surface} names an oversized loader"
            )
        raw = os.pread(fd, length, offset).split(b"\x00", 1)[0]
        try:
            token = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise _refuse(
                "INTERPRETER_UNRESOLVABLE", f"{surface} loader path is not UTF-8"
            ) from None
        candidate = Path(token)
        if not token or not candidate.is_absolute() or ".." in candidate.parts:
            raise _refuse(
                "INTERPRETER_INDIRECT", f"{surface} loader path is not absolute"
            )
        return str(candidate)
    return None


def _regular_file_sha256(path: Path, *, surface: str) -> str:
    try:
        info = os.lstat(path)
    except OSError:
        raise _refuse("ARTIFACT_MISSING", f"{surface} is missing") from None
    if not stat.S_ISREG(info.st_mode):
        raise _refuse("NOT_A_REGULAR_FILE", f"{surface} is not a regular file")
    try:
        return _file_digest(path).hex()
    except OSError:
        raise _refuse("ARTIFACT_UNREADABLE", f"{surface} could not be read") from None


# -- resolved values ---------------------------------------------------------


@dataclass(frozen=True)
class ResolvedSlot:
    name: str
    kind: str
    descriptor: Mapping[str, Any]
    slot_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "descriptor": dict(self.descriptor),
            "slot_hash": self.slot_hash,
        }


@dataclass(frozen=True)
class ResolvedBinding:
    """One Run's projection of one Binding generation — read once, then sealed."""

    schema_version: int
    generation_id: str
    manifest_sha256: str
    generation_hash: str
    slot_set_hash: str
    slots: Mapping[str, ResolvedSlot]
    session_compatibility_epoch: int
    contract_identity: Mapping[str, Any]
    provenance: Mapping[str, Any]

    @property
    def acceptance_receipt_ref(self) -> str | None:
        receipt = self.provenance.get("acceptance_receipt")
        if isinstance(receipt, Mapping):
            ref = receipt.get("ref")
            return ref if isinstance(ref, str) else None
        return None

    @property
    def acceptance_receipt_sha256(self) -> str | None:
        receipt = self.provenance.get("acceptance_receipt")
        if isinstance(receipt, Mapping):
            value = receipt.get("sha256")
            return value if isinstance(value, str) else None
        return None

    @property
    def frozen_agent_registration_hash(self) -> str | None:
        """The Registration digest this generation was accepted against.

        ``None`` for a generation that is not agent-scoped. Present as a
        property rather than read inline so the freeze invariant below reads as
        one comparison of two named facts.
        """
        value = self.contract_identity.get("agent_registration_hash")
        return value if isinstance(value, str) else None

    def slot(self, name: str) -> ResolvedSlot:
        try:
            return self.slots[name]
        except KeyError:
            raise _refuse("SLOT_ABSENT", f"binding declares no slot {name!r}") from None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generation_id": self.generation_id,
            "manifest_sha256": self.manifest_sha256,
            "generation_hash": self.generation_hash,
            "slot_set_hash": self.slot_set_hash,
            "slots": {name: slot.to_dict() for name, slot in sorted(self.slots.items())},
            "session_compatibility_epoch": self.session_compatibility_epoch,
            "contract_identity": dict(self.contract_identity),
            "acceptance_receipt_ref": self.acceptance_receipt_ref,
            "acceptance_receipt_sha256": self.acceptance_receipt_sha256,
        }


@dataclass(frozen=True)
class AdmittedRuntimeBinding:
    """One Run's Binding read plus the ownership policy that admitted it.

    Produced once per Run by ``arsd.admission`` and carried forward as a
    value. Holding the policy beside the generation keeps the spawn-boundary
    recheck honest: it re-proves artifact trust under the same rule that
    admitted the Run, not under a rule the Run could pick later.
    """

    resolved: ResolvedBinding
    ownership: TrustedOwnership
    # The agent this Run was admitted as, for a registration-scoped profile.
    # ``None`` for the three legacy profiles, which have no agent to carry.
    registration: AgentRegistration | None = None

    def __post_init__(self) -> None:
        """The freeze invariant: a generation binds the Registration it named.

        This is the one place a live Registration meets a generation, so it is
        the one place the comparison can happen — and putting it in
        ``__post_init__`` means no construction site can forget it, including
        test fixtures.

        Without it the generation's ``agent_registration_hash`` is decorative:
        an operator could validate and promote one Registration and then edit
        ``registration.json`` in place under the same agent, and the pointer,
        the manifest bytes, the manifest digest, the epoch, and the contract
        identity would all still be exactly right — because none of them is
        about the Registration's *contents*. The Run would then launch against
        argv tokens, selector domains, capability narrowing, or a mediation
        selection that nothing ever validated.

        Because ``agent_registration_hash`` excludes provenance, re-recording an
        acceptance, discovery, or canary receipt is not drift and stays
        compatible here; any compatibility-bearing edit is drift and fails
        closed.

        Symmetric on purpose. An agent-scoped generation carried without a
        Registration is refused, and a Registration carried alongside a
        generation that freezes none is refused too — otherwise "no agent
        identity" would be a way to opt out of the check.
        """
        frozen = self.resolved.frozen_agent_registration_hash
        live = None if self.registration is None else self.registration.registration_hash
        if frozen is None and live is None:
            return
        if frozen is None or live is None or not _digests_equal(frozen, live):
            raise _refuse(
                "REGISTRATION_HASH_MISMATCH",
                "the live agent registration is not the one this generation was "
                "accepted against",
            )


# -- the reader --------------------------------------------------------------


class BindingReader:
    """The only opener of a Binding root. Read-only, fail-closed, bounded."""

    def __init__(self, root: Path | str, *, ownership: TrustedOwnership) -> None:
        self._root = Path(root)
        self._ownership = ownership

    @property
    def root(self) -> Path:
        return self._root

    def read_registration(
        self, profile: AgentProfile, agent_id: str
    ) -> AgentRegistration:
        """The one ``registration.json`` read for an agent-scoped Run.

        Deliberately *not* folded into the generation manifest, despite costing
        a third read: folding would put agent identity inside ``generation_hash``,
        so an artifact-only bump would force re-authoring agent facts and a
        rollback would silently change the agent's ACP name.
        """
        self._require_agent_scope(profile, agent_id)
        agent_fd = self._open_agent(profile, agent_id)
        try:
            raw = _read_regular_file(
                agent_fd, REGISTRATION_FILENAME, surface="registration.json",
                missing_rule="REGISTRATION_ABSENT",
            )
            _READ_COUNTERS["registration"] += 1
            self._verify_entry(agent_fd, REGISTRATION_FILENAME, "registration.json")
        finally:
            os.close(agent_fd)
        payload = _decode_canonical(raw, surface="registration.json")
        try:
            return _parse_registration(
                payload, profile=profile, expected_agent_id=agent_component(agent_id)
            )
        except AgentRegistrationError as error:
            # The rule name is preserved verbatim: the grammar lives in the pure
            # leaf, but every refusal an operator sees still names one stable
            # rule from one place.
            raise _refuse(error.rule, error.message) from None

    def resolve_active(
        self, profile: AgentProfile, *, agent_id: str | None = None
    ) -> ResolvedBinding:
        """One ``active.json`` read plus one generation read. Nothing else.

        Both reads are anchored at the profile's own subtree — and, for an
        agent-scoped profile, at that agent's subtree inside it — so a root
        serves every registered profile and every registered agent at once, and
        a Run can only ever see the selection promoted for the pair resolving.

        This is a **generation-only primitive and admits no Agent Registration**.
        It returns what the generation says, including the Registration digest
        that generation was accepted against; it does not read, hold, or judge
        the Registration that is actually live. Pairing the two is
        :class:`AdmittedRuntimeBinding`'s job, and that pairing is where the
        freeze invariant is enforced — so a caller that wants an admitted
        runtime must go through it and cannot get one from here.
        """
        generation_id, manifest_sha256 = self.read_active(profile, agent_id=agent_id)
        return self.read_generation(
            generation_id,
            profile=profile,
            agent_id=agent_id,
            expected_manifest_sha256=manifest_sha256,
        )

    def read_active(
        self, profile: AgentProfile, *, agent_id: str | None = None
    ) -> tuple[str, str]:
        self._require_agent_scope(profile, agent_id)
        anchor_fd = self._open_anchor(profile, agent_id)
        try:
            raw = _read_regular_file(anchor_fd, ACTIVE_FILENAME, surface="active.json")
            _READ_COUNTERS["active"] += 1
            self._verify_entry(anchor_fd, ACTIVE_FILENAME, "active.json")
        finally:
            os.close(anchor_fd)
        payload = _decode_canonical(raw, surface="active.json")
        _require_fields(
            payload,
            pointer_fields(profile),
            rule="UNKNOWN_POINTER_FIELD",
            surface="active.json",
        )
        if payload.get("schema_version") != BINDING_SCHEMA_VERSION:
            raise _refuse("SCHEMA_VERSION", "active.json schema_version is unsupported")
        # The directory already separates the profiles; this makes the pointer
        # say so itself, so one moved or copied between subtrees is refused on
        # an explicit machine field rather than inherited from its filename.
        if payload.get("profile_id") != profile.profile_id:
            raise _refuse(
                "POINTER_PROFILE_MISMATCH",
                "active.json activates a different profile than the one resolving it",
            )
        if agent_id is not None:
            # One level deeper than the profile rule, for the same reason: a
            # pointer moved between two agent subtrees is refused on an explicit
            # machine field rather than on path separation alone.
            if payload.get("agent_id") != agent_component(agent_id):
                raise _refuse(
                    "POINTER_AGENT_MISMATCH",
                    "active.json activates a different agent than the one resolving it",
                )
        generation_id = _safe_generation_id(payload.get("generation_id"))
        manifest_sha256 = payload.get("manifest_sha256")
        if not _is_sha256(manifest_sha256):
            raise _refuse(
                "MANIFEST_DIGEST_MISSING", "active.json manifest_sha256 is malformed"
            )
        return generation_id, manifest_sha256

    def read_generation(
        self,
        generation_id: str,
        *,
        profile: AgentProfile,
        agent_id: str | None = None,
        expected_manifest_sha256: str | None = None,
    ) -> ResolvedBinding:
        self._require_agent_scope(profile, agent_id)
        generation_id = _safe_generation_id(generation_id)
        anchor_fd = self._open_anchor(profile, agent_id)
        try:
            generations_fd = _open_dir(
                GENERATIONS_DIRNAME, dir_fd=anchor_fd, surface="generations/"
            )
        finally:
            os.close(anchor_fd)
        try:
            self._verify_dir(generations_fd, "generations/")
            generation_fd = _open_dir(
                generation_id, dir_fd=generations_fd, surface="generation directory"
            )
        finally:
            os.close(generations_fd)
        try:
            self._verify_dir(generation_fd, "generation directory")
            raw = _read_regular_file(
                generation_fd, MANIFEST_FILENAME, surface="manifest.json"
            )
            _READ_COUNTERS["generation"] += 1
            self._verify_entry(generation_fd, MANIFEST_FILENAME, "manifest.json")
        finally:
            os.close(generation_fd)

        manifest_sha256 = hashlib.sha256(raw).hexdigest()
        if (
            expected_manifest_sha256 is not None
            and manifest_sha256 != expected_manifest_sha256
        ):
            raise _refuse(
                "MANIFEST_DIGEST_MISMATCH",
                "manifest does not match the digest the active pointer names",
            )
        payload = _decode_canonical(raw, surface="manifest.json")
        return self._project(
            payload,
            profile=profile,
            agent_id=agent_id,
            generation_id=generation_id,
            manifest_sha256=manifest_sha256,
        )

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _require_agent_scope(profile: AgentProfile, agent_id: str | None) -> None:
        """The scope gate, symmetric and before any filesystem query.

        A registration-scoped profile with no agent has no subtree to descend
        into; a legacy profile with an agent is a caller asking for a subtree
        that must not exist. Both are refused here rather than discovered as a
        missing directory later, so the refusal names the real fact.

        The component grammar runs here too, and that placement is the point:
        every public read calls this first, so caller text is judged before the
        Binding root is so much as opened.
        """
        requires = profile.contract.requires_agent_registration
        if requires and agent_id is None:
            raise _refuse(
                "AGENT_SCOPE_REQUIRED",
                f"profile {profile.profile_id} resolves only under an agent",
            )
        if not requires and agent_id is not None:
            raise _refuse(
                "AGENT_SCOPE_FORBIDDEN",
                f"profile {profile.profile_id} is not agent-scoped",
            )
        if agent_id is not None:
            agent_component(agent_id)

    def _open_root(self) -> int:
        return open_trusted_dir(
            self._root, ownership=self._ownership, surface="binding root"
        )

    def _open_anchor(self, profile: AgentProfile, agent_id: str | None) -> int:
        return _open_anchor_dir(
            self._root, profile, agent_id, ownership=self._ownership
        )

    def _open_agent(self, profile: AgentProfile, agent_id: str) -> int:
        return self._open_anchor(profile, agent_id)

    def _verify_dir(self, fd: int, surface: str) -> None:
        check_ownership(os.fstat(fd), self._ownership, surface)

    def _verify_entry(self, dir_fd: int, name: str, surface: str) -> None:
        check_ownership(os.lstat(name, dir_fd=dir_fd), self._ownership, surface)

    def _project(
        self,
        payload: Mapping[str, Any],
        *,
        profile: AgentProfile,
        agent_id: str | None,
        generation_id: str,
        manifest_sha256: str,
    ) -> ResolvedBinding:
        _require_fields(
            payload, _MANIFEST_FIELDS, rule="UNKNOWN_MANIFEST_FIELD", surface="manifest"
        )
        missing = sorted(_MANIFEST_FIELDS - set(payload))
        if missing:
            raise _refuse("MANIFEST_FIELD_MISSING", f"manifest omits {missing}")
        if payload["schema_version"] != BINDING_SCHEMA_VERSION:
            raise _refuse("SCHEMA_VERSION", "manifest schema_version is unsupported")
        if payload["generation_id"] != generation_id:
            raise _refuse(
                "GENERATION_ID_MISMATCH",
                "manifest generation_id does not match its directory",
            )

        identity = payload["contract_identity"]
        if identity is None:
            raise _refuse("CONTRACT_IDENTITY_ABSENT", "manifest declares no identity")
        if not isinstance(identity, dict):
            raise _refuse("MANIFEST_FIELD_TYPE", "contract_identity must be an object")
        expected_fields = contract_identity_fields(profile)
        _require_fields(
            identity,
            expected_fields,
            rule="UNKNOWN_MANIFEST_FIELD",
            surface="contract_identity",
        )
        if sorted(identity) != sorted(expected_fields):
            raise _refuse(
                "CONTRACT_IDENTITY_ABSENT", "contract_identity omits a machine field"
            )
        live = {
            "profile_id": profile.profile_id,
            "profile_revision": profile.revision,
            "adapter_contract_hash": profile.adapter_contract_hash(),
        }
        if agent_id is not None:
            # The agent half is checked separately so a generation authored for
            # a different agent is refused by *its own* rule: "this is not the
            # contract" and "this is not the agent" are different operator facts
            # and deserve different names.
            #
            # What is checkable *here* is only shape: this method reads one
            # manifest and never sees a Registration, so comparing the frozen
            # digest against the live one is not something it could do. That
            # comparison is the freeze invariant, and it belongs to
            # :class:`AdmittedRuntimeBinding`, the one object that holds both
            # halves. Folding the declared value into ``live`` below would make
            # the manifest satisfy itself — which is exactly the defect this
            # split exists to make unrepresentable.
            component = agent_component(agent_id)
            declared_agent = identity.get("agent_id")
            declared_hash = identity.get("agent_registration_hash")
            if declared_agent != component or not _is_sha256(declared_hash):
                raise _refuse(
                    "REGISTRATION_CONTRACT_MISMATCH",
                    "generation was accepted for a different agent registration",
                )
        # Compared over the contract fields only. The agent fields were judged
        # just above and are deliberately not re-judged here against themselves.
        declared_contract = {
            key: value
            for key, value in identity.items()
            if key in _CONTRACT_IDENTITY_FIELDS
        }
        if declared_contract != live:
            # A perfect provenance block never rescues this: acceptance rests
            # on the explicit machine fields only.
            raise _refuse(
                "CONTRACT_IDENTITY_MISMATCH",
                "generation was accepted under a different contract identity",
            )

        epoch = payload["session_compatibility_epoch"]
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch <= 0:
            raise _refuse(
                "EPOCH_NOT_POSITIVE", "session_compatibility_epoch must be a positive int"
            )

        provenance = payload["provenance"]
        self._require_provenance(provenance)

        slots = self._project_slots(payload["slots"], profile=profile)
        slot_set_hash = _sha256_hex(
            _canonical_json({name: slot.slot_hash for name, slot in sorted(slots.items())})
        )
        return ResolvedBinding(
            schema_version=BINDING_SCHEMA_VERSION,
            generation_id=generation_id,
            manifest_sha256=manifest_sha256,
            generation_hash=_sha256_hex(_canonical_json(payload)),
            slot_set_hash=slot_set_hash,
            slots=slots,
            session_compatibility_epoch=epoch,
            contract_identity=dict(identity),
            provenance=dict(provenance),
        )

    def _require_provenance(self, provenance: Any) -> None:
        """C4: the acceptance record must be complete and well formed.

        This is a *shape* rule, deliberately and only. Nothing here is an
        authorization input: a flawless receipt never rescues a mismatched
        contract identity, a wrong digest, or an untrusted artifact, and
        ``accepted_by`` is a human-readable provenance string rather than a
        machine identity gate. Requiring the record to be well formed and
        refusing to let it decide anything are two separate rules, and both
        hold.
        """
        if not isinstance(provenance, dict):
            raise _refuse("MANIFEST_FIELD_TYPE", "provenance must be an object")
        _require_fields(
            provenance,
            _PROVENANCE_FIELDS,
            rule="UNKNOWN_MANIFEST_FIELD",
            surface="provenance",
        )
        missing = sorted(_PROVENANCE_FIELDS - set(provenance))
        if missing:
            raise _refuse("PROVENANCE_FIELD_MISSING", f"provenance omits {missing}")
        for key in ("created_at", "accepted_by", "accepted_at"):
            _require_provenance_text(
                provenance[key], rule="PROVENANCE_FIELD_TYPE", surface=f"provenance {key}"
            )
        receipt = provenance["acceptance_receipt"]
        if not isinstance(receipt, dict):
            raise _refuse(
                "PROVENANCE_FIELD_TYPE", "acceptance_receipt must be an object"
            )
        _require_fields(
            receipt,
            _RECEIPT_FIELDS,
            rule="UNKNOWN_MANIFEST_FIELD",
            surface="acceptance_receipt",
        )
        missing = sorted(_RECEIPT_FIELDS - set(receipt))
        if missing:
            raise _refuse(
                "RECEIPT_FIELD_MISSING", f"acceptance_receipt omits {missing}"
            )
        _require_provenance_text(
            receipt["ref"], rule="RECEIPT_FIELD_TYPE", surface="acceptance receipt ref"
        )
        if not _is_sha256(receipt["sha256"]):
            raise _refuse(
                "RECEIPT_FIELD_TYPE", "acceptance receipt sha256 is not a sha256 digest"
            )

    def _project_slots(
        self, raw_slots: Any, *, profile: AgentProfile
    ) -> dict[str, ResolvedSlot]:
        if not isinstance(raw_slots, dict):
            raise _refuse("MANIFEST_FIELD_TYPE", "slots must be an object")
        contract = profile.contract
        declared = {slot.name: slot for slot in contract.binding_slots}
        unknown = sorted(set(raw_slots) - set(declared))
        if unknown:
            raise _refuse("UNKNOWN_SLOT", f"contract declares no slot(s) {unknown}")
        absent = sorted(set(declared) - set(raw_slots))
        if absent:
            raise _refuse("SLOT_ABSENT", f"generation omits declared slot(s) {absent}")

        resolved: dict[str, ResolvedSlot] = {}
        for name, declaration in sorted(declared.items()):
            body = raw_slots[name]
            if not isinstance(body, dict):
                raise _refuse("MANIFEST_FIELD_TYPE", f"slot {name!r} must be an object")
            kind = body.get("kind")
            if kind != declaration.kind:
                raise _refuse(
                    "SLOT_KIND_MISMATCH",
                    f"slot {name!r} is not the declared {declaration.kind}",
                )
            required = SLOT_DESCRIPTOR_FIELDS[declaration.kind]
            descriptor = {key: value for key, value in body.items() if key != "kind"}
            if sorted(descriptor) != sorted(required):
                raise _refuse(
                    "SLOT_DESCRIPTOR_FIELDS",
                    f"slot {name!r} descriptor is not exactly {sorted(required)}",
                )
            self._validate_descriptor(name, declaration.kind, descriptor)
            resolved[name] = ResolvedSlot(
                name=name,
                kind=declaration.kind,
                descriptor=descriptor,
                slot_hash=_sha256_hex(
                    _canonical_json(
                        {"name": name, "kind": declaration.kind, "descriptor": descriptor}
                    )
                ),
            )
        return resolved

    def _validate_descriptor(
        self, name: str, kind: str, descriptor: Mapping[str, Any]
    ) -> None:
        ownership = self._ownership
        if kind == SLOT_KIND_NATIVE_BINARY:
            path = _require_safe_absolute(descriptor["path"], f"slot {name} path")
            _require_version(descriptor["version"], name)
            if not _is_sha256(descriptor["sha256"]):
                raise _refuse("SLOT_DESCRIPTOR_FIELDS", f"slot {name} sha256 malformed")
            check_ancestors(path, ownership, f"slot {name} artifact")
            self._verify_artifact_file(path, descriptor["sha256"], f"slot {name} artifact")
            self._verify_interpreter(
                path,
                declared=descriptor["interpreter"],
                declared_digest=descriptor["interpreter_sha256"],
                surface=f"slot {name} artifact",
            )
        elif kind == SLOT_KIND_PACKAGE_TREE:
            package_root = _require_safe_absolute(
                descriptor["package_root"], f"slot {name} package_root"
            )
            launcher = _require_safe_absolute(
                descriptor["launcher_path"], f"slot {name} launcher_path"
            )
            interpreter = _require_safe_absolute(
                descriptor["interpreter_path"], f"slot {name} interpreter_path"
            )
            _require_version(descriptor["version"], name)
            for key in ("tree_sha256", "launcher_sha256", "interpreter_sha256"):
                if not _is_sha256(descriptor[key]):
                    raise _refuse(
                        "SLOT_DESCRIPTOR_FIELDS", f"slot {name} {key} is malformed"
                    )
            try:
                info = os.lstat(package_root)
            except OSError:
                raise _refuse(
                    "ARTIFACT_MISSING", f"slot {name} package_root is missing"
                ) from None
            if not stat.S_ISDIR(info.st_mode):
                raise _refuse(
                    "NOT_A_DIRECTORY", f"slot {name} package_root is not a directory"
                )
            check_ownership(info, ownership, f"slot {name} package_root")
            check_ancestors(package_root, ownership, f"slot {name} package_root")
            # C5: one closure, not three independent facts. A launcher outside
            # the hashed tree would load sibling code no digest ever froze, so
            # containment is a structural precondition of the tree digest
            # meaning anything about what the launcher runs.
            if launcher == package_root or not launcher.is_relative_to(package_root):
                raise _refuse(
                    "LAUNCHER_OUTSIDE_PACKAGE_ROOT",
                    f"slot {name} launcher is not inside its package_root",
                )
            observed_tree = package_tree_digest(package_root, ownership=ownership)
            if observed_tree != descriptor["tree_sha256"]:
                raise _refuse(
                    "PACKAGE_TREE_DIGEST_MISMATCH",
                    f"slot {name} package closure does not match its digest",
                )
            for path, digest, surface in (
                (launcher, descriptor["launcher_sha256"], f"slot {name} launcher"),
                (
                    interpreter,
                    descriptor["interpreter_sha256"],
                    f"slot {name} interpreter",
                ),
            ):
                check_ancestors(path, ownership, surface)
                self._verify_artifact_file(path, digest, surface)
            # The declared runtime must be the one the launcher actually uses;
            # a frozen Node digest proves nothing about a launcher that runs a
            # shell. The interpreter's own digest was verified just above.
            actual = required_interpreter(launcher, surface=f"slot {name} launcher")
            if actual is None:
                raise _refuse(
                    "INTERPRETER_NOT_REQUIRED",
                    f"slot {name} launcher needs no interpreter but declares one",
                )
            if actual != str(interpreter):
                raise _refuse(
                    "INTERPRETER_MISMATCH",
                    f"slot {name} launcher does not run the declared interpreter",
                )
        elif kind == SLOT_KIND_CONFIG_ROOT:
            # A config root is not an artifact: it holds runtime-managed state
            # the AGENT itself writes, so C5's non-writable artifact rule does
            # not apply. Only its shape is validated here; its mode/ownership
            # structure is proven at the spawn boundary.
            path = _require_safe_absolute(descriptor["path"], f"slot {name} path")
            try:
                info = os.lstat(path)
            except OSError:
                raise _refuse(
                    "ARTIFACT_MISSING", f"slot {name} config root is missing"
                ) from None
            if not stat.S_ISDIR(info.st_mode):
                raise _refuse(
                    "NOT_A_DIRECTORY", f"slot {name} config root is not a directory"
                )
        else:  # pragma: no cover - SLOT_KINDS is closed
            raise _refuse("SLOT_KIND_MISMATCH", f"slot {name} has an unknown kind")

    def _verify_interpreter(
        self,
        path: Path,
        *,
        declared: Any,
        declared_digest: Any,
        surface: str,
    ) -> None:
        """C5/C10: freeze the runtime the kernel selects, or refuse.

        ``interpreter: null`` is admissible for exactly one shape — an image
        that needs no external interpreter. A script or a dynamically loaded
        ELF must name its real runtime, and that runtime is then digested and
        ownership-checked like any other artifact.
        """
        actual = required_interpreter(path, surface=surface)
        if actual is None:
            if declared is not None or declared_digest is not None:
                raise _refuse(
                    "INTERPRETER_NOT_REQUIRED",
                    f"{surface} needs no interpreter but declares one",
                )
            return
        if declared is None:
            raise _refuse(
                "INTERPRETER_NOT_DECLARED",
                f"{surface} is run by an interpreter the descriptor never froze",
            )
        declared_path = _require_safe_absolute(declared, f"{surface} interpreter")
        if str(declared_path) != actual:
            raise _refuse(
                "INTERPRETER_MISMATCH",
                f"{surface} does not run the declared interpreter",
            )
        if not _is_sha256(declared_digest):
            raise _refuse(
                "SLOT_DESCRIPTOR_FIELDS", f"{surface} interpreter_sha256 is malformed"
            )
        check_ancestors(declared_path, self._ownership, f"{surface} interpreter")
        self._verify_artifact_file(
            declared_path, declared_digest, f"{surface} interpreter"
        )

    def _verify_artifact_file(self, path: Path, expected: str, surface: str) -> None:
        observed = _regular_file_sha256(path, surface=surface)
        if observed != expected:
            raise _refuse(
                "ARTIFACT_DIGEST_MISMATCH", f"{surface} does not match its frozen digest"
            )
        check_ownership(os.lstat(path), self._ownership, surface)


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _digests_equal(left: Any, right: Any) -> bool:
    """Exact equality of two hex digests, in constant time.

    Both operands are proven to be well-formed sha256 hex *before* comparison,
    so a malformed or non-string value is a mismatch rather than an exception —
    and ``compare_digest`` then sees two equal-length ASCII strings, which is
    the shape it requires. Length is not a side channel here because both sides
    are the same fixed width by construction.
    """
    if not _is_sha256(left) or not _is_sha256(right):
        return False
    return hmac.compare_digest(left, right)


_PROVENANCE_TEXT_MAX = 256


def _require_provenance_text(value: Any, *, rule: str, surface: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _PROVENANCE_TEXT_MAX
        or not all(ch.isprintable() for ch in value)
    ):
        raise _refuse(rule, f"{surface} must be a short printable string")


def _require_version(value: Any, slot_name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise _refuse("SLOT_DESCRIPTOR_FIELDS", f"slot {slot_name} version is malformed")
    if not all(ch.isprintable() for ch in value):
        raise _refuse("SLOT_DESCRIPTOR_FIELDS", f"slot {slot_name} version is unprintable")


# -- the code-owned version probe (C6) ---------------------------------------

# Hermetic probe environment: no HOME, no XDG root, no credential, no token,
# no proxy — a probe must not be able to reach a provider or read a secret.
_PROBE_ENV = {"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"}


def _sigchld_ignored() -> bool:
    """True when child exits are auto-reaped instead of left for this process.

    ``/proc/self/status`` is the kernel's own view, so it also sees a disposition
    inherited across ``exec`` that this interpreter never installed — which is the
    usual way a process acquires one. ``getsignal`` is the portable cross-check
    for a disposition Python set itself.
    """
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("SigIgn:"):
                mask = int(line.split()[1], 16)
                return bool(mask >> (int(signal.SIGCHLD) - 1) & 1)
    except (OSError, ValueError, IndexError):
        pass  # no /proc, or a shape this cannot parse: fall back to Python's view
    return signal.getsignal(signal.SIGCHLD) is signal.SIG_IGN


def _require_reapable_children() -> None:
    """Refuse *before* the fork when this process cannot own its children.

    A probe asserts two things: the exact version the CLI reported, and that
    nothing it started outlived it. Both rest on the process this code puts in
    charge of the probe — see :class:`_ProbeAnchor` — being this process's own
    child to wait for. With ``SIGCHLD`` ignored the kernel reaps every child the
    instant it exits, so that process is gone from under this one the moment it
    ends: its exit cannot be waited for, and the pid it holds — which *is* the
    probe's process-group id — is released while the group may still be live.

    Repairing the disposition here would mean mutating a signal setting shared
    with every other child of the calling process: flipping it back would
    auto-reap their statuses too, trading this problem for someone else's. So
    the probe is not started at all. A *caught* ``SIGCHLD`` cannot be
    distinguished from a reaping handler and is deliberately let through: the
    anchor makes it harmless rather than merely unlikely, because the probed
    CLI is not this process's child and no ``wait`` here can name it.
    """
    if os.name != "posix":  # pragma: no cover - non-POSIX host
        return
    if _sigchld_ignored():
        raise _refuse(
            "PROBE_REAPER_UNSAFE",
            "SIGCHLD is ignored, so a probe child's exit status cannot be "
            "collected and its process group cannot be provably contained",
        )


def probe_cli_version(
    *, executable: str, rule: VersionProbeRule, argv_prefix: Iterable[str] = ()
) -> str:
    """Run the contract's fixed non-prompt probe and parse its version.

    The only sanctioned way to learn an external CLI's real version. Never
    called on the admission path: ``validate`` and ``promote`` own it.
    """
    _require_reapable_children()
    argv = [*argv_prefix, executable, *rule.argv_suffix]
    argv[0] = _resolve_probe_program(argv[0])
    with tempfile.TemporaryDirectory(prefix="ars-probe-") as scratch:
        anchor = _ProbeAnchor.launch(argv, cwd=scratch)
        try:
            stdout, stderr, returncode = anchor.capture(
                limit=rule.max_output_bytes, timeout=rule.timeout_seconds
            )
        finally:
            # Containment is unconditional rather than a timeout remedy: every
            # way out of here — success, non-zero exit, deadline, bounded flood
            # or an internal error — tears the whole probe-owned group down.
            anchor.close()
    if returncode != 0:
        raise _refuse("PROBE_FAILED", "version probe exited non-zero")
    raw = stdout + b"\n" + stderr
    text = raw[: rule.max_output_bytes].decode("utf-8", errors="replace")
    parsed = rule.parse(text)
    if not parsed:
        raise _refuse("PROBE_UNPARSABLE", "version probe output carried no version")
    return parsed


def _resolve_probe_program(program: str) -> str:
    """The file a bare argv0 names, resolved against the *hermetic* ``PATH``.

    ``subprocess`` searches the environment it is handed; the anchor spawns with
    ``posix_spawn``, which searches nothing. Doing it here keeps the lookup in
    the same place as the environment that defines it. An unresolvable name is
    passed through so the failure is reported as one, not guessed at.
    """
    if "/" in program:
        return program
    for directory in _PROBE_ENV["PATH"].split(os.pathsep):
        candidate = os.path.join(directory, program)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return program


def _close_fds(*fds: int) -> None:
    for fd in fds:
        try:
            os.close(fd)
        except OSError:  # pragma: no cover - already closed
            pass


def _anchor_env() -> dict[str, str]:
    """The environment for the *anchor*, which is not the probed CLI's.

    The anchor is started with ``-I``, so no ``PYTHON*`` variable can steer it
    and its working directory is off ``sys.path``. What is left is the dynamic
    linker's search path, which some interpreter builds genuinely need in order
    to start at all. The CLI's environment is a separate thing entirely: the
    hermetic one the contract specifies, handed over in the request below.
    """
    env = dict(_PROBE_ENV)
    library_path = os.environ.get("LD_LIBRARY_PATH")
    if library_path:  # pragma: no cover - host-dependent interpreter build
        env["LD_LIBRARY_PATH"] = library_path
    return env


# The anchor's whole program. It runs as a separate interpreter, so nothing here
# shares state with the supervisor: it owns one process group, starts one child
# into it, reports that child's real exit over a pipe, and tears the group down
# from *inside* when its control pipe reaches EOF.
_PROBE_ANCHOR_SOURCE = r'''
import json
import os
import select
import signal
import sys

CONTROL, STATUS, OUT, ERR = (int(value) for value in sys.argv[1:5])


def say(line):
    try:
        os.write(STATUS, line)
    except OSError:
        pass


def leads_its_own_group():
    pid = os.getpid()
    return os.getpgrp() == pid and os.getsid(0) == pid


def teardown(code):
    # ``killpg(0, ...)`` names no number at all: the kernel resolves the target
    # from the calling task, and that task is alive because it is making the
    # call. There is no instant between deciding and delivering in which the
    # target could have become an unrelated group, because there is no target to
    # go stale -- and no number that could be recycled onto something else.
    if leads_its_own_group():
        say(b"T %d %d\n" % (os.getpid(), os.getpgrp()))
        try:
            os.killpg(0, signal.SIGKILL)
        except OSError:
            pass
    os._exit(code)


def status_line(status):
    if os.WIFEXITED(status):
        return b"X %d\n" % os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return b"K %d\n" % os.WTERMSIG(status)
    return b"?\n"


def main():
    # Refuse to be an anchor at all rather than ever signal a group this process
    # does not lead: the whole design rests on those being the same group.
    if not leads_its_own_group():
        os._exit(4)
    # One dedicated process, one child. ``setsigdef`` below starts the CLI from
    # the same dispositions ``subprocess(restore_signals=True)`` would have given
    # it -- a statement about the child, which the anchor must not also make
    # about itself, because the two need opposite things.
    ignored = []
    for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
        number = getattr(signal, name, None)
        if number is not None:
            ignored.append(number)
    defaults = [signal.SIGCHLD, *ignored]
    # ``SIGCHLD`` must be default here, or a disposition inherited across exec
    # would reap the child and take the status this process exists to report.
    # ``SIGPIPE`` must stay ignored here, or teardown becomes fatal on the very
    # path it is for: losing the supervisor closes the status reader and the
    # control writer in the same instant, so the report in ``teardown`` is
    # *expected* to fail exactly when the group most needs tearing down. Ignored,
    # it is an ``EPIPE`` that ``say`` swallows on the way to ``killpg``;
    # defaulted, it kills the anchor mid-report and leaves the group behind.
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    for number in ignored:
        signal.signal(number, signal.SIG_IGN)
    # Only the two descriptors the CLI is meant to have survive the exec below.
    for fd in (CONTROL, STATUS, OUT, ERR):
        os.set_inheritable(fd, False)

    request = b""
    while not request.endswith(b"\n"):
        chunk = os.read(CONTROL, 65536)
        if not chunk:
            teardown(3)  # torn down before the go-ahead: nothing was started
        request += chunk
    spec = json.loads(request)

    os.chdir(spec["cwd"])
    devnull = os.open(os.devnull, os.O_RDONLY)
    pid = None
    try:
        pid = os.posix_spawn(
            spec["argv"][0],
            spec["argv"],
            spec["env"],
            file_actions=[
                (os.POSIX_SPAWN_DUP2, devnull, 0),
                (os.POSIX_SPAWN_DUP2, OUT, 1),
                (os.POSIX_SPAWN_DUP2, ERR, 2),
            ],
            setsigdef=defaults,
        )
    except (OSError, ValueError) as error:
        say(b"E %d\n" % (getattr(error, "errno", None) or 0))
    os.close(devnull)
    # The supervisor closed its own copies at launch, so once these go the CLI
    # and its descendants are the only holders: the EOF it sees is about them.
    os.close(OUT)
    os.close(ERR)

    reported = pid is None
    while True:
        readable, _, _ = select.select([CONTROL], [], [], None if reported else 0.005)
        if readable and not os.read(CONTROL, 65536):
            teardown(0)
        if reported:
            continue
        try:
            waited, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:  # nothing here reaps, so this cannot happen
            say(b"?\n")
            reported = True
            continue
        if waited == pid:
            say(status_line(status))
            reported = True


main()
'''

_ANCHOR_TEARDOWN_SECONDS = 5.0


@dataclass(frozen=True)
class _ProbeOutcome:
    """The anchor's report on the CLI it owned.

    ``returncode`` is the CLI's real status in ``Popen``'s sign convention, and
    ``None`` means the anchor ended without ever reporting one — refused, never
    inferred. ``exec_failed`` is the CLI never having started at all.
    """

    returncode: int | None = None
    exec_failed: bool = False


def _parse_probe_outcome(fields: list[bytes]) -> _ProbeOutcome | None:
    if len(fields) == 2 and fields[1].isdigit():
        value = int(fields[1])
        if fields[0] == b"X":
            return _ProbeOutcome(returncode=value)
        if fields[0] == b"K":
            return _ProbeOutcome(returncode=-value)
        if fields[0] == b"E":
            return _ProbeOutcome(exec_failed=True)
    if fields == [b"?"]:
        return _ProbeOutcome()  # reported, and reported as unknowable
    return None


class _ProbeAnchor:
    """A code-owned process that *is* the probe's process group, for its whole life.

    C1's bound is only real if teardown reaches every descendant, and a group is
    only safe to tear down if its id cannot have become somebody else's. Before
    Linux 6.9 there is no syscall that makes a liveness observation and the
    process-group signal it would authorise atomic — ``pidfd`` names a process
    rather than a group, and does not reserve the number either, since the
    kernel releases a pid when the last task detaches from it and not when the
    last reference to it goes. Any ``killpg(pgid, ...)`` aimed by number is
    therefore check-then-signal, however fresh the check.

    So no number is ever aimed at. The probe runs under an anchor:

    * The anchor is started with ``start_new_session``, so it leads a group and
      session whose id is its own pid and which contains nothing but the probe.
    * The probed CLI is the *anchor's* child, not this process's. Its real exit
      status is collected by the anchor and arrives here over a pipe, where no
      reaper in this process can compete for it — ``wait`` here cannot even name
      it.
    * The anchor's only exit is EOF on its control pipe, whose sole write end is
      held here. While it is alive it is a task attached to that pid, so the
      number is allocated and the group is non-empty; and a live task cannot be
      reaped, because reaping is what a *zombie* undergoes. No reaper anywhere
      can release the group id while the anchor is in it.
    * Teardown is that EOF, and what the anchor does with it is ``killpg(0, ...)``
      on its own group, which the kernel resolves from the live caller. Closing
      the control pipe also covers this process dying without ever getting here:
      the kernel closes the write end, and the group tears itself down unattended.

    What the anchor cannot cover is being destroyed from outside. That is not
    quietly absorbed: the status never arrives, the probe refuses, and this
    process still aims at no group id, because none would be provable.
    """

    def __init__(
        self,
        process: "subprocess.Popen[bytes]",
        *,
        control_w: int,
        status_r: int,
        out_r: int,
        err_r: int,
    ) -> None:
        self._process = process
        self._control_w = control_w
        self._status_r = status_r
        self._out_r = out_r
        self._err_r = err_r
        self._pending = bytearray()
        self._outcome: _ProbeOutcome | None = None
        self._closed = False
        #: ``(pid, pgid)`` the anchor named on its way out, for callers that want
        #: the group it signalled stated by the process that signalled it.
        self.teardown_report: tuple[int, int] | None = None

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        """The anchor's own exit, which is the kernel's receipt for teardown.

        ``-SIGKILL`` is the anchor having been killed by the group signal it
        issued itself, which no process can do unless it was alive and in that
        group at the instant of the call.
        """
        return self._process.returncode

    @classmethod
    def launch(cls, argv: list[str], *, cwd: str) -> "_ProbeAnchor":
        control_r, control_w = os.pipe()
        status_r, status_w = os.pipe()
        out_r, out_w = os.pipe()
        err_r, err_w = os.pipe()
        theirs = (control_r, status_w, out_w, err_w)
        mine = (control_w, status_r, out_r, err_r)
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed argv, hermetic env
                [
                    sys.executable,
                    "-I",  # no PYTHON* variable and no cwd on ``sys.path``
                    "-c",
                    _PROBE_ANCHOR_SOURCE,
                    *(str(fd) for fd in theirs),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_anchor_env(),
                close_fds=True,
                pass_fds=theirs,
                # The anchor owns a fresh session, so the group it leads is the
                # whole probe tree, has no controlling terminal, and is never
                # the caller's own.
                start_new_session=True,
            )
        except (OSError, ValueError):
            _close_fds(*theirs, *mine)
            raise _refuse(
                "PROBE_FAILED", "version probe could not be supervised"
            ) from None
        # From here the anchor is the only holder of the write ends, which is
        # what makes EOF on this side a statement about the anchor and the CLI
        # rather than about a copy this process forgot to let go of.
        _close_fds(*theirs)
        anchor = cls(
            process, control_w=control_w, status_r=status_r, out_r=out_r, err_r=err_r
        )
        try:
            for fd in (status_r, out_r, err_r):
                os.set_blocking(fd, False)
            anchor._start(argv=argv, cwd=cwd)
        except BaseException:
            anchor.close()
            raise
        return anchor

    def _start(self, *, argv: list[str], cwd: str) -> None:
        """Hand the anchor its one and only instruction.

        The anchor blocks until this arrives, so a failure to deliver it is a
        probe that was never started rather than one left running: the write
        itself is the proof that there is something on the other side.
        """
        request = (
            json.dumps(
                {"argv": list(argv), "cwd": str(cwd), "env": dict(_PROBE_ENV)},
                ensure_ascii=True,
                sort_keys=True,
            ).encode("ascii")
            + b"\n"
        )
        view = memoryview(request)
        while view:
            try:
                view = view[os.write(self._control_w, view) :]
            except OSError:  # pragma: no cover - the anchor died in its exec
                raise _refuse(
                    "PROBE_FAILED", "version probe supervisor did not start"
                ) from None

    def capture(self, *, limit: int, timeout: float) -> tuple[bytes, bytes, int]:
        """Read both CLI pipes to EOF and the anchor's status, under one deadline.

        C1 promises a *bounded* probe, which is a memory bound rather than a
        post-hoc slice: output past the bound is read and discarded so the CLI
        never blocks on a full pipe, but it is never accumulated. All three
        descriptors are drained through one selector, so none can deadlock
        waiting on another, and the deadline covers the whole exchange.

        A status the anchor never reported is refused rather than inferred, and
        refused *promptly*: once the anchor's side of the status pipe is gone
        nothing further can arrive, so there is nothing left to wait out.
        """
        deadline = time.monotonic() + timeout
        streams = {self._out_r: bytearray(), self._err_r: bytearray()}
        selector = selectors.DefaultSelector()
        timed_out = False
        lost = False
        try:
            for fd in (*streams, self._status_r):
                selector.register(fd, selectors.EVENT_READ)
            while selector.get_map() and not lost:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(timeout=min(remaining, 0.2)):
                    fd = key.fd
                    try:
                        chunk = os.read(fd, 1 << 16)
                    except BlockingIOError:
                        continue
                    except OSError:  # pragma: no cover - torn down mid-read
                        chunk = b""
                    if not chunk:
                        selector.unregister(fd)
                        if fd == self._status_r and self._outcome is None:
                            lost = True
                        continue
                    if fd == self._status_r:
                        self._pending += chunk
                        self._consume()
                        if self._outcome is not None:
                            selector.unregister(fd)
                        continue
                    buffer = streams[fd]
                    if len(buffer) < limit:
                        buffer += chunk[: limit - len(buffer)]
                    # Anything past the bound is deliberately dropped, not stored.
        finally:
            selector.close()
        if timed_out:
            raise _refuse("PROBE_TIMEOUT", "version probe exceeded its bound")
        outcome = self._outcome
        if outcome is None or (outcome.returncode is None and not outcome.exec_failed):
            raise _refuse(
                "PROBE_STATUS_LOST",
                "the probe's supervisor ended without reporting an exit status",
            )
        if outcome.exec_failed:
            raise _refuse("PROBE_FAILED", "version probe could not be executed")
        assert outcome.returncode is not None
        return (
            bytes(streams[self._out_r]),
            bytes(streams[self._err_r]),
            outcome.returncode,
        )

    def close(self) -> None:
        """Tear the probe's group down, then hand back every descriptor.

        Teardown is a *message*, not a signal aimed from here: closing the
        control pipe is the anchor's only exit, and what it does on the way out
        is signal its own group. This process names no process-group id at any
        point, so it has none to mis-aim.
        """
        if self._closed:
            return
        self._closed = True
        try:
            _close_fds(self._control_w)
            self._collect_parting_words()
            try:
                self._process.wait(timeout=_ANCHOR_TEARDOWN_SECONDS)
            except subprocess.TimeoutExpired:  # pragma: no cover - SIGKILL is final
                pass
        finally:
            _close_fds(self._out_r, self._err_r, self._status_r)

    def _collect_parting_words(self) -> None:
        """Read the status pipe until the anchor's side of it is gone."""
        deadline = time.monotonic() + _ANCHOR_TEARDOWN_SECONDS
        selector = selectors.DefaultSelector()
        try:
            selector.register(self._status_r, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:  # pragma: no cover - the anchor exits at once
                    return
                if not selector.select(timeout=min(remaining, 0.2)):
                    continue
                try:
                    chunk = os.read(self._status_r, 1 << 16)
                except BlockingIOError:  # pragma: no cover - select said otherwise
                    continue
                except OSError:  # pragma: no cover - torn down mid-read
                    return
                if not chunk:
                    return
                self._pending += chunk
                self._consume()
        finally:
            selector.close()

    def _consume(self) -> None:
        """Parse whole lines out of the status pipe; the first exit reported wins."""
        while True:
            line, separator, rest = bytes(self._pending).partition(b"\n")
            if not separator:
                return
            self._pending[:] = rest
            fields = line.split()
            if not fields:
                continue  # pragma: no cover - the anchor writes no blank lines
            if fields[0] == b"T":
                if len(fields) == 3 and all(field.isdigit() for field in fields[1:]):
                    self.teardown_report = (int(fields[1]), int(fields[2]))
                continue
            if self._outcome is None:
                self._outcome = _parse_probe_outcome(fields)


def probe_slot_version(
    resolved: ResolvedBinding,
    profile: AgentProfile,
    *,
    rule: VersionProbeRule | None = None,
) -> str:
    """Probe the CLI artifact the contract's ``cli_slot`` names.

    ``rule`` lets an agent-scoped caller pass the suffix its registration
    selected. Only the suffix is ever registration-owned: the parser, the
    timeout, and the output bound stay exactly as the contract froze them,
    because :class:`AgentRegistration` copies them from it rather than
    accepting them.
    """
    contract = profile.contract
    if contract.cli_slot is None:
        raise _refuse("PROBE_FAILED", "contract declares no CLI slot to probe")
    probe = rule if rule is not None else contract.version_probe
    slot = resolved.slot(contract.cli_slot)
    if slot.kind == SLOT_KIND_NATIVE_BINARY:
        return probe_cli_version(
            executable=str(slot.descriptor["path"]), rule=probe
        )
    return probe_cli_version(
        executable=str(slot.descriptor["launcher_path"]), rule=probe
    )


def declared_cli_version(resolved: ResolvedBinding, profile: AgentProfile) -> str:
    slot = resolved.slot(profile.contract.cli_slot or "")
    return str(slot.descriptor["version"])


# -- operator entry points ---------------------------------------------------


def validate_generation(
    root: Path | str,
    generation_id: str,
    *,
    profile: AgentProfile,
    ownership: TrustedOwnership,
    agent_id: str | None = None,
    probe: bool = True,
) -> ResolvedBinding:
    """Full fail-closed validation of one generation against the live contract.

    With ``probe`` the real external CLI version is obtained through the
    contract's code-owned probe and compared with the Binding — a manifest's
    version string alone is never proof. For an agent-scoped profile the
    registration is read first, so the probe runs the suffix that agent's
    registration actually selected.
    """
    reader = BindingReader(root, ownership=ownership)
    registration = (
        None if agent_id is None else reader.read_registration(profile, agent_id)
    )
    resolved = reader.read_generation(
        generation_id, profile=profile, agent_id=agent_id
    )
    # The same freeze invariant the runtime pair enforces, applied through the
    # same object rather than restated — so a generation whose Registration has
    # drifted can never be validated, promoted, rolled back to, or otherwise
    # blessed. Constructing the pair is the check; the value is discarded.
    AdmittedRuntimeBinding(
        resolved=resolved, ownership=ownership, registration=registration
    )
    if probe and profile.contract.cli_slot is not None:
        rule = None if registration is None else registration.version_probe
        observed = probe_slot_version(resolved, profile, rule=rule)
        declared = declared_cli_version(resolved, profile)
        if observed != declared:
            raise _refuse(
                "PROBE_VERSION_MISMATCH",
                f"probe reported {observed!r}; the generation declares {declared!r}",
            )
        # C6: the object returned here is the object a caller may promote, so
        # it has to still be the object that was probed. A generation replaced
        # while the probe was running would otherwise be activated on the
        # strength of a version nobody ever observed.
        try:
            confirmed = reader.read_generation(
                generation_id,
                profile=profile,
                agent_id=agent_id,
                expected_manifest_sha256=resolved.manifest_sha256,
            )
        except BindingRefusal:
            raise _refuse(
                "GENERATION_CHANGED", "the generation changed while it was probed"
            ) from None
        if confirmed.generation_hash != resolved.generation_hash:
            raise _refuse(
                "GENERATION_CHANGED", "the generation changed while it was probed"
            )
    return resolved


def read_active_pointer(
    root: Path | str,
    *,
    profile: AgentProfile,
    ownership: TrustedOwnership,
    agent_id: str | None = None,
) -> tuple[str, str] | None:
    """One profile's (or agent's) promoted generation, or ``None`` for none.

    ``None`` is "nothing is promoted here", which is an ordinary state in a
    root that serves several profiles and several agents. A pre-0.5.2 root is
    not that state and is not absorbed into it: it raises. Neither is a scope
    error — asking for the wrong shape is a refusal, not an empty answer.
    """
    try:
        return BindingReader(root, ownership=ownership).read_active(
            profile, agent_id=agent_id
        )
    except BindingRefusal as refusal:
        if refusal.rule in (
            "OPEN_FAILED",
            "NOT_A_REGULAR_FILE",
            "PROFILE_BINDING_ABSENT",
            "AGENT_BINDING_ABSENT",
        ):
            return None
        raise


def write_active_pointer(
    root: Path | str,
    resolved: ResolvedBinding,
    *,
    profile: AgentProfile,
    ownership: TrustedOwnership,
    agent_id: str | None = None,
) -> Path:
    """Atomically replace one profile's ``active.json`` — the only file ARS writes.

    The write obeys the same ancestor policy as the reader: the root is opened
    through the verified dirfd walk, the descent to ``profiles/<profile_id>/``
    is the reader's own, and every step — create, fsync, chmod, replace — is
    dirfd-relative, so a redirected or rewritable ancestor can never become a
    promotion target and no pathname is re-resolved between the proof and the
    write.

    The target directory is fsynced after ``os.replace`` so the rename is
    durable, not merely atomic, across a crash. Because the replaced file lives
    inside the profile's own subtree, promoting one profile cannot disable,
    overwrite, or race another's selection — concurrently or in sequence.

    ARS creates no directory here: ``profiles/<profile_id>/generations/`` is
    operator-authored storage, and a promotion into a subtree that does not
    exist is refused rather than materialized.

    No symlink is created, no other file is touched, and no daemon is
    restarted: admission re-reads the pointer per Run, so a promotion takes
    effect on the next Run and never re-points a sealed one.
    """
    root = Path(root)
    BindingReader._require_agent_scope(profile, agent_id)
    if resolved.contract_identity.get("profile_id") != profile.profile_id:
        # Unreachable through projection, which already matched the live
        # contract — stated here so the write side carries the invariant rather
        # than inheriting it.
        raise _refuse(
            "POINTER_PROFILE_MISMATCH",
            "refusing to promote a generation accepted for a different profile",
        )
    payload = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "profile_id": profile.profile_id,
        "generation_id": resolved.generation_id,
        "manifest_sha256": resolved.manifest_sha256,
    }
    if agent_id is not None:
        component = agent_component(agent_id)
        if resolved.contract_identity.get("agent_id") != component:
            raise _refuse(
                "POINTER_AGENT_MISMATCH",
                "refusing to promote a generation accepted for a different agent",
            )
        payload["agent_id"] = component
    data = _canonical_json(payload).encode("utf-8")
    profile_fd = _open_anchor_dir(root, profile, agent_id, ownership=ownership)
    try:
        temp_name = f".active-{os.getpid()}-{os.urandom(8).hex()}.tmp"
        fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o644,
            dir_fd=profile_fd,
        )
        try:
            try:
                os.write(fd, data)
                os.fchmod(fd, 0o644)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(
                temp_name, ACTIVE_FILENAME, src_dir_fd=profile_fd, dst_dir_fd=profile_fd
            )
        except BaseException:
            try:
                os.unlink(temp_name, dir_fd=profile_fd)
            except OSError:
                pass
            raise
        os.fsync(profile_fd)
    finally:
        os.close(profile_fd)
    return active_pointer_path(root, profile.profile_id, agent_id=agent_id)

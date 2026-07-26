"""The only reader of an operator-owned Runtime Binding root (PRD R13, C7).

Layer 2 of the three runtime-authority layers. A Binding root is operator
storage outside the repository and outside ``.agent-run-supervisor/``: ARS
opens it read-only and never creates, repairs, or migrates it. The single
exception is the operator command surface, which atomically replaces
``active.json`` and nothing else.

```text
<binding_root>/
├── active.json                     # regular file, atomically replaced
└── generations/<generation_id>/
    └── manifest.json               # immutable once written
```

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

from .profile import (
    ARTIFACT_SLOT_KINDS,
    SLOT_DESCRIPTOR_FIELDS,
    SLOT_KIND_CONFIG_ROOT,
    SLOT_KIND_NATIVE_BINARY,
    SLOT_KIND_PACKAGE_TREE,
    AgentProfile,
    VersionProbeRule,
)

BINDING_SCHEMA_VERSION = 1

ACTIVE_FILENAME = "active.json"
GENERATIONS_DIRNAME = "generations"
MANIFEST_FILENAME = "manifest.json"

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
_POINTER_FIELDS = frozenset({"schema_version", "generation_id", "manifest_sha256"})
_CONTRACT_IDENTITY_FIELDS = frozenset(
    {"profile_id", "profile_revision", "adapter_contract_hash"}
)
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

_READ_COUNTERS: dict[str, int] = {"active": 0, "generation": 0}


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


def _read_regular_file(dir_fd: int, name: str, *, surface: str) -> bytes:
    try:
        info = os.lstat(name, dir_fd=dir_fd)
    except OSError:
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

    Deterministic ordering by POSIX-relative path, length-prefixed so a name
    boundary can never be shifted into a neighbour, and bounded: an oversized
    tree is refused rather than sampled. Symlinks and special files inside the
    closure are refused — an immutable package root contains regular files and
    directories only.

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
                        "PACKAGE_TREE_TOO_LARGE", "package closure exceeds the byte bound"
                    )
                _absorb(digest, relative, "f", _file_digest(child))
            else:
                raise _refuse(
                    "PACKAGE_TREE_UNSAFE_ENTRY",
                    "package closure contains a symlink or special file",
                )
            if entries > max_entries:
                raise _refuse(
                    "PACKAGE_TREE_TOO_LARGE", "package closure exceeds the entry bound"
                )
    return digest.hexdigest()


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


# -- the reader --------------------------------------------------------------


class BindingReader:
    """The only opener of a Binding root. Read-only, fail-closed, bounded."""

    def __init__(self, root: Path | str, *, ownership: TrustedOwnership) -> None:
        self._root = Path(root)
        self._ownership = ownership

    @property
    def root(self) -> Path:
        return self._root

    def resolve_active(self, profile: AgentProfile) -> ResolvedBinding:
        """One ``active.json`` read plus one generation read. Nothing else."""
        generation_id, manifest_sha256 = self.read_active()
        return self.read_generation(
            generation_id, profile=profile, expected_manifest_sha256=manifest_sha256
        )

    def read_active(self) -> tuple[str, str]:
        root_fd = self._open_root()
        try:
            self._verify_dir(root_fd, "binding root")
            raw = _read_regular_file(root_fd, ACTIVE_FILENAME, surface="active.json")
            _READ_COUNTERS["active"] += 1
            self._verify_entry(root_fd, ACTIVE_FILENAME, "active.json")
        finally:
            os.close(root_fd)
        payload = _decode_canonical(raw, surface="active.json")
        _require_fields(
            payload, _POINTER_FIELDS, rule="UNKNOWN_POINTER_FIELD", surface="active.json"
        )
        if payload.get("schema_version") != BINDING_SCHEMA_VERSION:
            raise _refuse("SCHEMA_VERSION", "active.json schema_version is unsupported")
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
        expected_manifest_sha256: str | None = None,
    ) -> ResolvedBinding:
        generation_id = _safe_generation_id(generation_id)
        root_fd = self._open_root()
        try:
            self._verify_dir(root_fd, "binding root")
            generations_fd = _open_dir(
                GENERATIONS_DIRNAME, dir_fd=root_fd, surface="generations/"
            )
        finally:
            os.close(root_fd)
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
            generation_id=generation_id,
            manifest_sha256=manifest_sha256,
        )

    # -- internals ---------------------------------------------------------

    def _open_root(self) -> int:
        return open_trusted_dir(
            self._root, ownership=self._ownership, surface="binding root"
        )

    def _verify_dir(self, fd: int, surface: str) -> None:
        check_ownership(os.fstat(fd), self._ownership, surface)

    def _verify_entry(self, dir_fd: int, name: str, surface: str) -> None:
        check_ownership(os.lstat(name, dir_fd=dir_fd), self._ownership, surface)

    def _project(
        self,
        payload: Mapping[str, Any],
        *,
        profile: AgentProfile,
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
        _require_fields(
            identity,
            _CONTRACT_IDENTITY_FIELDS,
            rule="UNKNOWN_MANIFEST_FIELD",
            surface="contract_identity",
        )
        if sorted(identity) != sorted(_CONTRACT_IDENTITY_FIELDS):
            raise _refuse(
                "CONTRACT_IDENTITY_ABSENT", "contract_identity omits a machine field"
            )
        live = {
            "profile_id": profile.profile_id,
            "profile_revision": profile.revision,
            "adapter_contract_hash": profile.adapter_contract_hash(),
        }
        if identity != live:
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


def probe_slot_version(resolved: ResolvedBinding, profile: AgentProfile) -> str:
    """Probe the CLI artifact the contract's ``cli_slot`` names."""
    contract = profile.contract
    if contract.cli_slot is None:
        raise _refuse("PROBE_FAILED", "contract declares no CLI slot to probe")
    slot = resolved.slot(contract.cli_slot)
    if slot.kind == SLOT_KIND_NATIVE_BINARY:
        return probe_cli_version(
            executable=str(slot.descriptor["path"]), rule=contract.version_probe
        )
    return probe_cli_version(
        executable=str(slot.descriptor["launcher_path"]), rule=contract.version_probe
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
    probe: bool = True,
) -> ResolvedBinding:
    """Full fail-closed validation of one generation against the live contract.

    With ``probe`` the real external CLI version is obtained through the
    contract's code-owned probe and compared with the Binding — a manifest's
    version string alone is never proof.
    """
    reader = BindingReader(root, ownership=ownership)
    resolved = reader.read_generation(generation_id, profile=profile)
    if probe and profile.contract.cli_slot is not None:
        observed = probe_slot_version(resolved, profile)
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
    root: Path | str, *, ownership: TrustedOwnership
) -> tuple[str, str] | None:
    """The currently promoted generation, or ``None`` when none is promoted."""
    try:
        return BindingReader(root, ownership=ownership).read_active()
    except BindingRefusal as refusal:
        if refusal.rule in ("OPEN_FAILED", "NOT_A_REGULAR_FILE"):
            return None
        raise


def write_active_pointer(
    root: Path | str, resolved: ResolvedBinding, *, ownership: TrustedOwnership
) -> Path:
    """Atomically replace ``active.json`` — the only file ARS ever writes here.

    The write obeys the same ancestor policy as the reader: the root is opened
    through the verified dirfd walk and every step — create, fsync, chmod,
    replace — is dirfd-relative, so a redirected or rewritable ancestor can
    never become a promotion target and no pathname is re-resolved between the
    proof and the write.

    The root directory itself is fsynced after ``os.replace`` so the rename is
    durable, not merely atomic, across a crash.

    No symlink is created, no other file is touched, and no daemon is
    restarted: admission re-reads the pointer per Run, so a promotion takes
    effect on the next Run and never re-points a sealed one.
    """
    root = Path(root)
    payload = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "generation_id": resolved.generation_id,
        "manifest_sha256": resolved.manifest_sha256,
    }
    data = _canonical_json(payload).encode("utf-8")
    root_fd = open_trusted_dir(root, ownership=ownership, surface="binding root")
    try:
        temp_name = f".active-{os.getpid()}-{os.urandom(8).hex()}.tmp"
        fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o644,
            dir_fd=root_fd,
        )
        try:
            try:
                os.write(fd, data)
                os.fchmod(fd, 0o644)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(
                temp_name, ACTIVE_FILENAME, src_dir_fd=root_fd, dst_dir_fd=root_fd
            )
        except BaseException:
            try:
                os.unlink(temp_name, dir_fd=root_fd)
            except OSError:
                pass
            raise
        os.fsync(root_fd)
    finally:
        os.close(root_fd)
    return root / ACTIVE_FILENAME

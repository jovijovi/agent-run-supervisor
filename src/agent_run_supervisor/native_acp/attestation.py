"""Spawn-boundary attestation of the per-Run sealed runtime identity (D10/R13).

A Run whose admission sealed a :class:`SealedRuntimeIdentity` gets one
deterministic check point immediately before its child is spawned:

``pin → credential-alias refusal → hash-through-inode → credential-root
structure → project-config closure → race seam → liveness/absence recheck →
write-once report``

The gate is **detect-and-refuse at every Run**, not a runtime cage. It proves
that the artifacts about to be executed, the credential root about to be used,
and the configuration surfaces the agent will read are exactly the ones this
Run *sealed at admission* — not a profile constant — on both sides of the race
window, and refuses fail-closed otherwise. It never OS-sandboxes the launched
runtime and makes no claim about an actor racing the residual window inside a
single spawn.

Artifacts are pinned as ``O_PATH`` descriptors and hashed by reopening those
descriptors through ``/proc/self/fd``, so identity is bound to inodes rather
than to pathnames. On PASS the pin of the image that will actually be exec'd
survives as ``exec_fd`` and the spawn exec's that descriptor directly, leaving
zero residual swap window:

- ``wrapped_acp`` pins the source-frozen interpreter and the source-frozen ACP
  adapter entry, and proves two package closures: the source-frozen adapter
  tree the entry is a member of, and the Binding-sealed downstream CLI. ARS can
  fd-pin neither the siblings Node resolves around argv[1] nor the CLI the
  adapter reopens later, so the honest guarantee for both is path-and-closure
  immutability under an operator-owned root, not descriptor identity — plus, for
  the adapter, the refusal of any module-resolution root above that closure.
- ``direct_acp`` pins the single Binding-sealed executable that is both the
  AGENT CLI and the ACP implementation, and exec's that descriptor.

Every persisted value is a path, a hex digest, an octal mode, an errno name, a
boolean, or an integer. Credential bytes are never read, and digests of
credential-bearing files are never computed: the declared credential root and
``auth.json`` are identified structurally *before* any content hash runs, and
an artifact whose pin resolves to that inode is refused rather than hashed.
The module owns its refusal type (:class:`AttestationRefusal`) and imports
nothing from ``run_task``, so the dependency direction stays
``run_task → attestation``.
"""

from __future__ import annotations

import errno as _errno
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from . import storage
from .profile import path_within_root
from .runtime_binding import BindingRefusal, TrustedOwnership  # noqa: F401 - re-exported
from .runtime_binding import check_ancestors as _check_ancestors
from .runtime_binding import check_ownership as _check_ownership
from .runtime_binding import package_tree_digest

ATTESTATION_SCHEMA_VERSION = 1
ATTESTATION_FILENAME = "attestation.json"

# Refusal classes. Every check row maps to exactly one of them. An artifact
# that is not the frozen, non-rewritable one *is* a runtime identity mismatch,
# so the artifact-trust rows reuse that class rather than inventing vocabulary.
RUNTIME_IDENTITY_MISMATCH = "RUNTIME_IDENTITY_MISMATCH"
PROJECT_CONFIG_LAYER_PRESENT = "PROJECT_CONFIG_LAYER_PRESENT"
CREDENTIAL_ROOT_VIOLATION = "CREDENTIAL_ROOT_VIOLATION"

_CHECK_CLASSES: dict[str, str] = {
    "platform_unsupported": RUNTIME_IDENTITY_MISMATCH,
    "node_pin": RUNTIME_IDENTITY_MISMATCH,
    "adapter_entry_pin": RUNTIME_IDENTITY_MISMATCH,
    "cli_pin": RUNTIME_IDENTITY_MISMATCH,
    "node_credential_alias": CREDENTIAL_ROOT_VIOLATION,
    "adapter_entry_credential_alias": CREDENTIAL_ROOT_VIOLATION,
    "cli_credential_alias": CREDENTIAL_ROOT_VIOLATION,
    "node_sha256": RUNTIME_IDENTITY_MISMATCH,
    "adapter_entry_sha256": RUNTIME_IDENTITY_MISMATCH,
    "cli_sha256": RUNTIME_IDENTITY_MISMATCH,
    "cli_artifact_trust": RUNTIME_IDENTITY_MISMATCH,
    "cli_package_closure": RUNTIME_IDENTITY_MISMATCH,
    "adapter_package_closure": RUNTIME_IDENTITY_MISMATCH,
    "adapter_resolution_escape": RUNTIME_IDENTITY_MISMATCH,
    "cli_interpreter_sha256": RUNTIME_IDENTITY_MISMATCH,
    "argv_node_binding": RUNTIME_IDENTITY_MISMATCH,
    "argv_interpreter_prefix_binding": RUNTIME_IDENTITY_MISMATCH,
    "argv_adapter_entry_binding": RUNTIME_IDENTITY_MISMATCH,
    "argv_cli_binding": RUNTIME_IDENTITY_MISMATCH,
    "env_cli_path_binding": RUNTIME_IDENTITY_MISMATCH,
    "node_binding_lost": RUNTIME_IDENTITY_MISMATCH,
    "adapter_entry_binding_lost": RUNTIME_IDENTITY_MISMATCH,
    "cli_binding_lost": RUNTIME_IDENTITY_MISMATCH,
    "cli_package_closure_recheck": RUNTIME_IDENTITY_MISMATCH,
    "adapter_package_closure_recheck": RUNTIME_IDENTITY_MISMATCH,
    "adapter_resolution_escape_recheck": RUNTIME_IDENTITY_MISMATCH,
    "credential_root_not_declared": CREDENTIAL_ROOT_VIOLATION,
    "project_config_not_declared": PROJECT_CONFIG_LAYER_PRESENT,
    "credential_root_structure": CREDENTIAL_ROOT_VIOLATION,
    "auth_json_structure": CREDENTIAL_ROOT_VIOLATION,
    "config_toml_absent": CREDENTIAL_ROOT_VIOLATION,
    "credential_root_binding_lost": CREDENTIAL_ROOT_VIOLATION,
    "auth_json_binding_lost": CREDENTIAL_ROOT_VIOLATION,
    "config_toml_absence_recheck": CREDENTIAL_ROOT_VIOLATION,
    "project_config_closure": PROJECT_CONFIG_LAYER_PRESENT,
    "project_config_closure_recheck": PROJECT_CONFIG_LAYER_PRESENT,
}

AUTH_FILENAME = "auth.json"
CREDENTIAL_ROOT_MODE = 0o700
AUTH_FILE_MODE = 0o600
PROJECT_CONFIG_DIRNAME = ".codex"
PROJECT_CONFIG_FILENAME = "config.toml"
# The one directory name Node's module resolution searches on a parent walk.
NODE_MODULES_DIRNAME = "node_modules"
_HASH_BLOCK_BYTES = 1 << 20
_CREDENTIAL_ALIAS_EXPECTED = "distinct from credential file"
_NOT_DECLARED = "not declared"

DEFAULT_PROJECT_CONFIG_RELPATH = f"{PROJECT_CONFIG_DIRNAME}/{PROJECT_CONFIG_FILENAME}"

# Documented test-only injection point sitting exactly between the
# evidence-producing checks and the recheck. Product default is a no-op; tests
# monkeypatch it to tamper "in the window" and prove the recheck refuses.
_POST_ATTESTATION_HOOK: Callable[[], None] | None = None


class AttestationRefusal(Exception):
    """Fail-closed refusal raised by :func:`attest_spawn_boundary`.

    The public, typed refusal surface of this module. ``code`` is the failing
    check's refusal class; callers translate it into their own pre-dispatch
    failure shape.
    """

    def __init__(self, *, code: str, failing_check: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.failing_check = failing_check
        self.message = message


class _CheckFailed(Exception):
    """Internal control flow: one named check produced a FAIL row."""

    def __init__(self, name: str, expected: str | None, observed: str | None) -> None:
        super().__init__(name)
        self.name = name
        self.expected = expected
        self.observed = observed


def _effective_uid() -> int:
    """Seam over the process euid so ownership checks stay testable."""
    return os.geteuid()


def _errno_name(exc: OSError) -> str:
    return _errno.errorcode.get(exc.errno, f"errno_{exc.errno}")


def _file_type(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "regular file"
    return "special file"


@dataclass(frozen=True)
class ArtifactClosure:
    """The complete executable code closure of one external CLI artifact (C5).

    ``native_binary`` freezes a regular-file digest. ``package_tree`` freezes
    the immutable package root's tree digest, the launcher identity, and the
    required interpreter identity — because a launcher hash alone never freezes
    the sibling code that launcher loads, and ARS must not claim that it does.
    """

    kind: str
    path: str
    sha256: str
    version: str
    package_root: str | None = None
    tree_sha256: str | None = None
    interpreter_path: str | None = None
    interpreter_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("native_binary", "package_tree"):
            raise ValueError(f"unknown artifact closure kind: {self.kind!r}")
        _require_absolute(self.path, "artifact path")
        _require_digest(self.sha256, "artifact sha256")
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("artifact version must be a non-empty string")
        if self.kind == "package_tree":
            _require_absolute(self.package_root, "artifact package_root")
            _require_digest(self.tree_sha256, "artifact tree_sha256")
            _require_absolute(self.interpreter_path, "artifact interpreter_path")
            _require_digest(self.interpreter_sha256, "artifact interpreter_sha256")
        elif (self.interpreter_path is None) != (self.interpreter_sha256 is None):
            # A native binary may need no interpreter, but a half-declared one
            # would seal a path whose identity nothing froze.
            raise ValueError("artifact interpreter path and digest travel together")
        elif self.interpreter_path is not None:
            _require_absolute(self.interpreter_path, "artifact interpreter_path")
            _require_digest(self.interpreter_sha256, "artifact interpreter_sha256")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "path": self.path,
            "sha256": self.sha256,
            "version": self.version,
        }
        for name in (
            "package_root",
            "tree_sha256",
            "interpreter_path",
            "interpreter_sha256",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload


@dataclass(frozen=True)
class SealedRuntimeIdentity:
    """The runtime identity one Run sealed at admission (R13 layer 3).

    Its source half (interpreter, adapter entry, ACP name/protocol) comes from
    the profile's :class:`~.profile.AdapterContract`; its deployment half
    (``cli``, credential-root path) comes from the Binding generation that
    admission read exactly once. ``agent_info_version`` is present only where
    the ACP-reported version is itself a *source* artifact fact — a wrapped
    adapter — and is ``None`` for ``direct_acp``, where it reports the
    deployed executable and may never be asserted equal to a CLI ``--version``.

    A ``None`` surface never drops rows silently: the report records an
    explicit ``*_not_declared`` row instead.
    """

    launch_kind: str
    agent_info_name: str
    protocol_version: str
    cli: ArtifactClosure
    agent_info_version: str | None = None
    cli_path_env: str | None = None
    node_path: str | None = None
    node_sha256: str | None = None
    adapter_entry_path: str | None = None
    adapter_entry_sha256: str | None = None
    adapter_package_root: str | None = None
    adapter_tree_sha256: str | None = None
    interpreter_argv_prefix: tuple[str, ...] = ()
    credential_root_env: str | None = None
    credential_root_path: str | None = None
    project_config_relpath: str | None = None

    def __post_init__(self) -> None:
        if self.launch_kind not in ("wrapped_acp", "direct_acp"):
            raise ValueError(f"unknown launch kind: {self.launch_kind!r}")
        for field_name in ("agent_info_name", "protocol_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"sealed runtime identity {field_name} must be a non-empty string"
                )
        if not self.protocol_version.isdigit():
            raise ValueError(
                "sealed runtime identity protocol_version must be a decimal string"
            )
        if self.launch_kind == "wrapped_acp":
            _require_absolute(self.node_path, "sealed node_path")
            _require_digest(self.node_sha256, "sealed node_sha256")
            _require_absolute(self.adapter_entry_path, "sealed adapter_entry_path")
            _require_digest(self.adapter_entry_sha256, "sealed adapter_entry_sha256")
            _require_absolute(self.adapter_package_root, "sealed adapter_package_root")
            _require_digest(self.adapter_tree_sha256, "sealed adapter_tree_sha256")
            if not path_within_root(self.adapter_package_root, self.adapter_entry_path):
                raise ValueError(
                    "sealed adapter_entry_path must be inside adapter_package_root"
                )
            if not self.interpreter_argv_prefix:
                raise ValueError(
                    "a wrapped runtime must seal its interpreter_argv_prefix: the "
                    "options that close the interpreter's out-of-closure search "
                    "are part of the identity, not an incidental argv literal"
                )
            if not self.cli_path_env:
                raise ValueError("a wrapped runtime must bind its CLI path env key")
        elif (
            self.node_path is not None
            or self.adapter_entry_path is not None
            or self.adapter_package_root is not None
            or self.adapter_tree_sha256 is not None
            or self.interpreter_argv_prefix
        ):
            raise ValueError(
                "a direct runtime seals no interpreter, adapter entry, adapter "
                "closure, or interpreter_argv_prefix"
            )
        for field_name in ("cli_path_env", "credential_root_env"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not isinstance(value, str) or not value or "=" in value:
                raise ValueError(
                    f"sealed runtime identity {field_name} must be an "
                    "environment variable name"
                )
        relpath = self.project_config_relpath
        if relpath is not None:
            parts = Path(relpath).parts
            if not relpath or Path(relpath).is_absolute() or not parts or ".." in parts:
                raise ValueError(
                    "sealed runtime identity project_config_relpath must be a "
                    "relative path without parent references"
                )

    @property
    def exec_path(self) -> str:
        """The image the spawn will exec: the interpreter, or the CLI itself."""
        return self.node_path if self.launch_kind == "wrapped_acp" else self.cli.path

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "launch_kind": self.launch_kind,
            "agent_info_name": self.agent_info_name,
            "protocol_version": self.protocol_version,
            "cli": self.cli.to_dict(),
        }
        for name in (
            "agent_info_version",
            "cli_path_env",
            "node_path",
            "node_sha256",
            "adapter_entry_path",
            "adapter_entry_sha256",
            "adapter_package_root",
            "adapter_tree_sha256",
            "credential_root_env",
            "credential_root_path",
            "project_config_relpath",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        # Omit-when-empty, so a direct_acp launch record stays byte-identical.
        if self.interpreter_argv_prefix:
            payload["interpreter_argv_prefix"] = list(self.interpreter_argv_prefix)
        return payload


def _require_absolute(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError(f"{name} must be an absolute path")


def _require_digest(value: Any, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise ValueError(f"{name} must be a sha256 hex digest")


@dataclass(frozen=True)
class AttestationCheck:
    name: str
    expected: str | None
    observed: str | None
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "expected": self.expected,
            "observed": self.observed,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class AttestationReport:
    checks: tuple[AttestationCheck, ...]
    binding: Mapping[str, Mapping[str, Any]]
    passed: bool
    schema_version: int = ATTESTATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pass": self.passed,
            "checks": [check.to_dict() for check in self.checks],
            "binding": {
                artifact: dict(facts) for artifact, facts in self.binding.items()
            },
        }


@dataclass(frozen=True)
class SpawnAttestation:
    """``exec_fd`` pins the image the spawn will exec.

    ``wrapped_acp``: the frozen interpreter. ``direct_acp``: the sealed AGENT
    CLI itself, so C10's descriptor-exec holds for the one artifact that is
    both CLI and ACP implementation.
    """

    report: AttestationReport
    exec_fd: int


def project_config_closure(
    effective_cwd: str, relpath: str = DEFAULT_PROJECT_CONFIG_RELPATH
) -> str | None:
    """D11a: the inclusive ancestor chain of ``effective_cwd`` up to ``/``.

    Returns the first ``<dir>/<relpath>`` that exists as a path entry of any
    type (``lexists``: a symlinked ``.codex/``, a symlinked or broken
    ``config.toml``, and a directory all count), else ``None``. The adapter
    loads layered configuration by cwd, so any such layer on the chain is a
    configuration surface ARS did not freeze. ``relpath`` defaults to the
    Codex surface, so existing callers keep their exact behavior.

    This is the shared "first hit walking up" predicate. Its second use is
    :func:`adapter_resolution_escape`, which asks the same question of Node's
    module-resolution chain — both are surfaces a runtime reaches by walking
    parents, and both must give the same answer pre-hook and post-hook.

    One pure predicate, two call sites per check — the pre-hook pass and the
    post-hook recheck cannot diverge.
    """
    directory = Path(effective_cwd)
    while True:
        candidate = str(directory / relpath)
        if os.path.lexists(candidate):
            return candidate
        parent = directory.parent
        if parent == directory:
            return None
        directory = parent


@dataclass(frozen=True)
class _CredentialIdentity:
    """Structural identity of the declared credential root and ``auth.json``.

    Built from ``stat``/``lstat`` facts only — no credential byte is read and
    no digest of a credential-bearing file is ever computed. Both the link
    itself and whatever it currently resolves to are recorded, because
    aliasing is an inode question and never a pathname question: a retargeted
    symlink, a hardlink under another name, and a symlinked root all collapse
    onto the same ``(st_dev, st_ino)`` pair.

    Identification is deliberately best-effort per probe. A root or file this
    cannot resolve is one no artifact can be aliased onto through that path
    either, and the R8 structure rows below still refuse it with their own
    named row — so a probe failure never silently widens the gate.
    """

    inodes: frozenset[tuple[int, int]]

    @classmethod
    def identify(cls, root: str | None) -> "_CredentialIdentity":
        if not root:
            return cls(frozenset())
        auth = os.path.join(root, AUTH_FILENAME)
        found: set[tuple[int, int]] = set()
        for path, follow in ((root, True), (root, False), (auth, True), (auth, False)):
            try:
                info = os.stat(path) if follow else os.lstat(path)
            except OSError:
                continue
            found.add((info.st_dev, info.st_ino))
        return cls(frozenset(found))

    def matches(self, info: os.stat_result) -> bool:
        return (info.st_dev, info.st_ino) in self.inodes

    @staticmethod
    def observed(info: os.stat_result) -> str:
        return f"credential file {info.st_dev}:{info.st_ino}"


class _AttestationState:
    """Accumulates rows, inode bindings, and open pins for one attestation."""

    def __init__(self) -> None:
        self.checks: list[AttestationCheck] = []
        self.binding: dict[str, dict[str, Any]] = {}
        self.fds: dict[str, int] = {}

    # -- rows ------------------------------------------------------------

    def record(self, name: str, expected: str | None, observed: str | None) -> None:
        self.checks.append(
            AttestationCheck(
                name=name, expected=expected, observed=observed, passed=True
            )
        )

    def report(self, *, passed: bool) -> AttestationReport:
        return AttestationReport(
            checks=tuple(self.checks), binding=dict(self.binding), passed=passed
        )

    # -- descriptors -----------------------------------------------------

    def close_all(self) -> None:
        for fd in self.fds.values():
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds.clear()

    def detach(self, artifact: str) -> int:
        return self.fds.pop(artifact)

    # -- checks ----------------------------------------------------------

    def pin_artifact(self, artifact: str, name: str, path: str) -> None:
        """O_PATH pin of a regular file; the descriptor carries the identity.

        Always ``O_NOFOLLOW``: a Binding names an immutable versioned path, and
        the Binding reader already refused a symlinked artifact, so a symlink
        appearing here is a swap between admission and spawn.
        """
        flags = os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise _CheckFailed(name, "regular file", _errno_name(exc)) from None
        self.fds[artifact] = fd
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _CheckFailed(name, "regular file", _file_type(info.st_mode))
        self.binding[artifact] = {
            "dev": info.st_dev,
            "ino": info.st_ino,
            "recheck_passed": False,
        }
        self.record(name, "regular file", "regular file")

    def require_not_credential_alias(
        self, artifact: str, name: str, credential: _CredentialIdentity
    ) -> None:
        """A content-hashed artifact must never be the declared credential file.

        The comparison is on the pinned inode, so a retargeted symlink, a
        hardlink, or an outright path swap are all caught — and caught before
        :meth:`hash_artifact` could read a single credential byte.
        """
        info = os.fstat(self.fds[artifact])
        if credential.matches(info):
            raise _CheckFailed(
                name, _CREDENTIAL_ALIAS_EXPECTED, credential.observed(info)
            )
        self.record(name, _CREDENTIAL_ALIAS_EXPECTED, "distinct")

    def hash_artifact(
        self,
        artifact: str,
        name: str,
        expected_digest: str,
        *,
        credential: _CredentialIdentity,
    ) -> None:
        """Stream sha256 through the pinned inode, never through the path."""
        pin_fd = self.fds[artifact]
        pinned = os.fstat(pin_fd)
        if credential.matches(pinned):
            # Unreachable through _perform, whose alias rows already refused
            # this pin. Kept local to the hashing function so no future
            # reordering can reach the reopen with a credential inode.
            raise _CheckFailed(
                f"{artifact}_credential_alias",
                _CREDENTIAL_ALIAS_EXPECTED,
                credential.observed(pinned),
            )
        try:
            read_fd = os.open(f"/proc/self/fd/{pin_fd}", os.O_RDONLY | os.O_CLOEXEC)
        except OSError as exc:
            raise _CheckFailed(name, expected_digest, _errno_name(exc)) from None
        try:
            reopened = os.fstat(read_fd)
            if (reopened.st_dev, reopened.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise _CheckFailed(name, expected_digest, "pin_inode_diverged")
            digest = hashlib.sha256()
            while True:
                block = os.read(read_fd, _HASH_BLOCK_BYTES)
                if not block:
                    break
                digest.update(block)
        finally:
            os.close(read_fd)
        observed = digest.hexdigest()
        if observed != expected_digest:
            raise _CheckFailed(name, expected_digest, observed)
        self.record(name, expected_digest, observed)

    def equality(self, name: str, expected: str, observed: str | None) -> None:
        if observed != expected:
            raise _CheckFailed(name, expected, observed)
        self.record(name, expected, observed)

    def sequence_equality(
        self, name: str, expected: tuple[str, ...], observed: tuple[str, ...]
    ) -> None:
        """Exact token sequence, rendered for the report only after comparing.

        Order and arity are both part of the claim, so the comparison is on the
        tuples; the joined text exists solely so the row stays a readable
        expected/observed pair like every other one.
        """
        if tuple(observed) != tuple(expected):
            raise _CheckFailed(name, " ".join(expected), " ".join(observed))
        self.record(name, " ".join(expected), " ".join(observed))

    def pin_credential_root(
        self, name: str, root: str | None, *, env_key: str
    ) -> None:
        """Structure only — the root's contents are never read."""
        if not root:
            raise _CheckFailed(name, env_key, "missing")
        try:
            info = os.lstat(root)
        except OSError as exc:
            raise _CheckFailed(name, "directory", _errno_name(exc)) from None
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise _CheckFailed(name, "directory", _file_type(info.st_mode))
        uid = _effective_uid()
        if info.st_uid != uid:
            raise _CheckFailed(name, f"uid {uid}", f"uid {info.st_uid}")
        mode = stat.S_IMODE(info.st_mode)
        if mode != CREDENTIAL_ROOT_MODE:
            raise _CheckFailed(name, oct(CREDENTIAL_ROOT_MODE), oct(mode))
        try:
            fd = os.open(
                root,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC,
            )
        except OSError as exc:
            raise _CheckFailed(name, "directory", _errno_name(exc)) from None
        self.fds["credential_root"] = fd
        reopened = os.fstat(fd)
        if (reopened.st_dev, reopened.st_ino) != (info.st_dev, info.st_ino):
            raise _CheckFailed(name, "stable root inode", "root_inode_diverged")
        self.binding["credential_root"] = {
            "dev": info.st_dev,
            "ino": info.st_ino,
            "recheck_passed": False,
        }
        self.record(name, oct(CREDENTIAL_ROOT_MODE), oct(mode))

    def pin_auth_file(self, name: str) -> None:
        """Open, stat, and close ``auth.json`` — the bytes are never read."""
        root_fd = self.fds["credential_root"]
        try:
            fd = os.open(
                AUTH_FILENAME,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=root_fd,
            )
        except OSError as exc:
            raise _CheckFailed(name, "regular file", _errno_name(exc)) from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise _CheckFailed(name, "regular file", _file_type(info.st_mode))
            uid = _effective_uid()
            if info.st_uid != uid:
                raise _CheckFailed(name, f"uid {uid}", f"uid {info.st_uid}")
            mode = stat.S_IMODE(info.st_mode)
            if mode != AUTH_FILE_MODE:
                raise _CheckFailed(name, oct(AUTH_FILE_MODE), oct(mode))
            self.binding["auth_json"] = {
                "dev": info.st_dev,
                "ino": info.st_ino,
                "recheck_passed": False,
            }
        finally:
            os.close(fd)
        self.record(name, oct(AUTH_FILE_MODE), oct(mode))

    def require_no_config_toml(self, name: str) -> None:
        """An ambient ``config.toml`` would merge into the frozen CODEX_CONFIG.

        Evaluated through the pinned root descriptor, so no path is re-walked.
        """
        try:
            os.lstat("config.toml", dir_fd=self.fds["credential_root"])
        except FileNotFoundError:
            self.record(name, "absent", "absent")
            return
        except OSError as exc:
            raise _CheckFailed(name, "absent", _errno_name(exc)) from None
        raise _CheckFailed(name, "absent", "present")

    def require_no_project_config(
        self, name: str, effective_cwd: str, relpath: str
    ) -> None:
        offender = project_config_closure(effective_cwd, relpath)
        if offender is not None:
            raise _CheckFailed(name, "absent", offender)
        self.record(name, "absent", "absent")

    # -- post-hook liveness recheck --------------------------------------

    def recheck_artifact(self, artifact: str, name: str, path: str) -> None:
        flags = os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW
        pinned = self.binding[artifact]
        expected = f"{pinned['dev']}:{pinned['ino']}"
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise _CheckFailed(name, expected, _errno_name(exc)) from None
        try:
            info = os.fstat(fd)
        finally:
            os.close(fd)
        self._compare_binding(artifact, name, expected, info)

    def recheck_credential_root(self, name: str, root: str) -> None:
        pinned = self.binding["credential_root"]
        expected = f"{pinned['dev']}:{pinned['ino']}"
        try:
            info = os.lstat(root)
        except OSError as exc:
            raise _CheckFailed(name, expected, _errno_name(exc)) from None
        self._compare_binding("credential_root", name, expected, info)

    def recheck_auth_file(self, name: str) -> None:
        pinned = self.binding["auth_json"]
        expected = f"{pinned['dev']}:{pinned['ino']}"
        try:
            info = os.lstat(AUTH_FILENAME, dir_fd=self.fds["credential_root"])
        except OSError as exc:
            raise _CheckFailed(name, expected, _errno_name(exc)) from None
        self._compare_binding("auth_json", name, expected, info)

    def _compare_binding(
        self, artifact: str, name: str, expected: str, info: os.stat_result
    ) -> None:
        observed = f"{info.st_dev}:{info.st_ino}"
        if observed != expected:
            raise _CheckFailed(name, expected, observed)
        self.binding[artifact]["recheck_passed"] = True
        self.record(name, expected, observed)


def _refusal_for(failure: _CheckFailed) -> AttestationRefusal:
    return AttestationRefusal(
        code=_CHECK_CLASSES.get(failure.name, RUNTIME_IDENTITY_MISMATCH),
        failing_check=failure.name,
        message=(
            f"spawn-boundary attestation refused at {failure.name}: "
            f"expected {failure.expected!r}, observed {failure.observed!r}"
        ),
    )


def _require_artifact_trust(
    state: _AttestationState, name: str, path: str, ownership: TrustedOwnership
) -> None:
    """C5 ownership: the artifact and every ancestor resist the service UID.

    Re-proven here rather than trusted from admission, because ownership and
    mode are exactly the properties an attacker would change inside the window
    between the Binding read and the spawn.
    """
    try:
        _check_ownership(os.lstat(path), ownership, "artifact")
        _check_ancestors(Path(path), ownership, "artifact")
    except BindingRefusal as refusal:
        raise _CheckFailed(name, "operator-owned and non-writable", refusal.rule) from None
    state.record(name, "operator-owned and non-writable", "trusted")


def _require_package_closure(
    state: _AttestationState,
    name: str,
    root: str | None,
    expected: str | None,
    ownership: TrustedOwnership,
) -> None:
    """A package root's sibling code, not just the file that launches it.

    Used for both closures a wrapped Run depends on: the Binding-sealed
    downstream CLI, and the source-frozen ACP adapter. Neither can be fd-pinned
    on the runtime's behalf — Node reopens the adapter tree by path after this
    gate closes its descriptors, and the adapter reopens the CLI later still —
    so the guarantee is path-and-closure immutability under an operator-owned
    root: the whole tree digest plus the ownership/mode of the root and every
    ancestor.
    """
    if root is None:
        state.record(name, _NOT_DECLARED, _NOT_DECLARED)
        return
    try:
        _check_ownership(os.lstat(root), ownership, "package root")
        _check_ancestors(Path(root), ownership, "package root")
        observed = package_tree_digest(Path(root), ownership=ownership)
    except BindingRefusal as refusal:
        raise _CheckFailed(name, expected, refusal.rule) from None
    except OSError as exc:
        raise _CheckFailed(name, expected, _errno_name(exc)) from None
    if observed != expected:
        raise _CheckFailed(name, expected, observed)
    state.record(name, expected, observed)


def adapter_resolution_escape(package_root: str) -> str | None:
    """The first module-resolution root Node would search *outside* the closure.

    ``NODE_MODULES_PATHS`` walks the entry's parent chain and searches
    ``<dir>/node_modules`` at every level, skipping the ``node_modules``
    components themselves. Everything that walk reaches at or below the closure
    root is inside the frozen tree digest; everything above it is not. So a
    ``node_modules`` on the chain strictly above the root is code the adapter
    can load and no digest froze — the closure would be complete only by
    accident of that directory not existing.

    Returns the offending path, or ``None`` when the closure is the last word.
    """
    return project_config_closure(
        str(Path(package_root).parent), NODE_MODULES_DIRNAME
    )


def _require_no_resolution_escape(
    state: _AttestationState, name: str, package_root: str | None
) -> None:
    if package_root is None:
        state.record(name, _NOT_DECLARED, _NOT_DECLARED)
        return
    offender = adapter_resolution_escape(package_root)
    if offender is not None:
        raise _CheckFailed(name, "absent", offender)
    state.record(name, "absent", "absent")


def _perform(
    state: _AttestationState,
    *,
    expected: SealedRuntimeIdentity,
    launch: Any,
    fixed_env: Mapping[str, str],
    effective_cwd: str,
    ownership: TrustedOwnership,
) -> None:
    if not hasattr(os, "O_PATH"):
        # Never degrade to pathname trust: descriptor pinning is the gate.
        raise _CheckFailed("platform_unsupported", "os.O_PATH", "unavailable")

    wrapped = expected.launch_kind == "wrapped_acp"

    # 1. Pin every artifact by descriptor before anything is measured.
    if wrapped:
        state.pin_artifact("node", "node_pin", expected.node_path)
        state.pin_artifact(
            "adapter_entry", "adapter_entry_pin", expected.adapter_entry_path
        )
    state.pin_artifact("cli", "cli_pin", expected.cli.path)

    # 1b. Identify the sealed credential root and auth.json structurally —
    #     stat facts only — and refuse any pinned artifact that resolves to
    #     that inode. This runs before step 2 so an aliased artifact is never
    #     read: hashing a credential file would both leak a credential-derived
    #     digest into the FAIL report and read bytes this gate must not touch.
    credential_env_key = expected.credential_root_env
    root = expected.credential_root_path if credential_env_key else None
    credential = _CredentialIdentity.identify(root)
    if wrapped:
        state.require_not_credential_alias("node", "node_credential_alias", credential)
        state.require_not_credential_alias(
            "adapter_entry", "adapter_entry_credential_alias", credential
        )
    state.require_not_credential_alias("cli", "cli_credential_alias", credential)

    # 2. Hash through the pinned inodes and bind argv/env to the same paths.
    if wrapped:
        state.hash_artifact(
            "node", "node_sha256", expected.node_sha256, credential=credential
        )
        state.hash_artifact(
            "adapter_entry",
            "adapter_entry_sha256",
            expected.adapter_entry_sha256,
            credential=credential,
        )
    state.hash_artifact(
        "cli", "cli_sha256", expected.cli.sha256, credential=credential
    )
    # C5 applies to every artifact the launch depends on, not only the one the
    # Binding named. Node and the adapter entry are source-frozen, but a frozen
    # digest is a statement about bytes that anyone able to write them can
    # falsify — and the adapter entry is the one artifact ARS hands onward by
    # path, so its trust boundary is the only thing standing between this gate
    # and Node's own open of argv[1].
    _require_artifact_trust(state, "cli_artifact_trust", expected.cli.path, ownership)
    if wrapped:
        _require_artifact_trust(
            state, "node_artifact_trust", expected.node_path, ownership
        )
        _require_artifact_trust(
            state,
            "adapter_entry_artifact_trust",
            expected.adapter_entry_path,
            ownership,
        )
    _require_package_closure(
        state,
        "cli_package_closure",
        expected.cli.package_root,
        expected.cli.tree_sha256,
        ownership,
    )
    if wrapped:
        # The adapter is frozen as a closure, not as one entry file: Node
        # resolves the entry's siblings and the hoisted dependencies above it
        # by walking up from argv[1], and none of that is covered by the entry
        # digest two rows above.
        _require_package_closure(
            state,
            "adapter_package_closure",
            expected.adapter_package_root,
            expected.adapter_tree_sha256,
            ownership,
        )
        _require_no_resolution_escape(
            state, "adapter_resolution_escape", expected.adapter_package_root
        )
    if expected.cli.interpreter_path is not None:
        # The interpreter is an artifact of the launch like any other: a frozen
        # digest without a trust boundary is a statement about bytes anyone able
        # to write them can falsify.
        _require_artifact_trust(
            state, "cli_interpreter_trust", expected.cli.interpreter_path, ownership
        )
        observed = _digest_path(expected.cli.interpreter_path)
        state.equality(
            "cli_interpreter_sha256", expected.cli.interpreter_sha256, observed
        )

    argv = tuple(getattr(launch, "argv", ()) or ())
    if wrapped:
        state.equality(
            "argv_node_binding", expected.node_path, argv[0] if len(argv) > 0 else None
        )
        # The frozen interpreter options are identity, not decoration: dropped,
        # reordered, or misspelled, the child regains a module-resolution root
        # this closure never froze. Compared as a sequence rather than as joined
        # text so no token boundary can be shifted into a neighbour.
        prefix = expected.interpreter_argv_prefix
        entry_index = 1 + len(prefix)
        state.sequence_equality(
            "argv_interpreter_prefix_binding", prefix, argv[1:entry_index]
        )
        state.equality(
            "argv_adapter_entry_binding",
            expected.adapter_entry_path,
            argv[entry_index] if len(argv) > entry_index else None,
        )
        state.equality(
            "env_cli_path_binding",
            expected.cli.path,
            fixed_env.get(expected.cli_path_env),
        )
    else:
        # One executable is both AGENT CLI and ACP implementation: argv[0] is
        # the sealed artifact, and no env key carries a downstream CLI path.
        state.equality(
            "argv_cli_binding", expected.cli.path, argv[0] if len(argv) > 0 else None
        )

    # 3. Credential-root structure (never its bytes). A runtime whose CLI owns
    #    its own credential storage declares no root; the absence is recorded
    #    as its own row so a missing check is never invisible in the report.
    if credential_env_key is not None:
        state.pin_credential_root(
            "credential_root_structure", root, env_key=credential_env_key
        )
        state.pin_auth_file("auth_json_structure")
        state.require_no_config_toml("config_toml_absent")
    else:
        state.record("credential_root_not_declared", _NOT_DECLARED, _NOT_DECLARED)

    # 4. Workspace project-config closure (only for runtimes that freeze one).
    project_relpath = expected.project_config_relpath
    if project_relpath is not None:
        state.require_no_project_config(
            "project_config_closure", effective_cwd, project_relpath
        )
    else:
        state.record("project_config_not_declared", _NOT_DECLARED, _NOT_DECLARED)

    # 5. Deterministic race seam (product no-op).
    hook = _POST_ATTESTATION_HOOK
    if hook is not None:
        hook()

    # 6. Liveness + absence recheck: every predicate the spawn depends on,
    #    re-evaluated post-hook so both sides of the window must hold.
    if wrapped:
        state.recheck_artifact("node", "node_binding_lost", expected.node_path)
        state.recheck_artifact(
            "adapter_entry",
            "adapter_entry_binding_lost",
            expected.adapter_entry_path,
        )
        _require_artifact_trust(
            state, "node_trust_recheck", expected.node_path, ownership
        )
        _require_artifact_trust(
            state,
            "adapter_entry_trust_recheck",
            expected.adapter_entry_path,
            ownership,
        )
        # The inode rows above cannot see an in-place rewrite, which is exactly
        # the shape a same-inode content swap takes. Node opens argv[1] by path
        # after this gate closes its descriptor, so the adapter entry's bytes
        # are re-proven through the still-open pin on the far side of the
        # window. The residual gap — between this row and Node's own open — is
        # closed by the trust rows, not by a descriptor: argv is contract-frozen
        # and cannot be redirected to /proc/self/fd.
        state.hash_artifact(
            "adapter_entry",
            "adapter_entry_sha256_recheck",
            expected.adapter_entry_sha256,
            credential=credential,
        )
        # The entry's own pin says nothing about the siblings Node will resolve
        # around it, so the closure and its escape are both re-proven on the
        # far side of the window.
        _require_package_closure(
            state,
            "adapter_package_closure_recheck",
            expected.adapter_package_root,
            expected.adapter_tree_sha256,
            ownership,
        )
        _require_no_resolution_escape(
            state, "adapter_resolution_escape_recheck", expected.adapter_package_root
        )
    state.recheck_artifact("cli", "cli_binding_lost", expected.cli.path)
    _require_artifact_trust(state, "cli_trust_recheck", expected.cli.path, ownership)
    if expected.cli.interpreter_path is not None:
        _require_artifact_trust(
            state,
            "cli_interpreter_trust_recheck",
            expected.cli.interpreter_path,
            ownership,
        )
    _require_package_closure(
        state,
        "cli_package_closure_recheck",
        expected.cli.package_root,
        expected.cli.tree_sha256,
        ownership,
    )
    if credential_env_key is not None:
        state.recheck_credential_root("credential_root_binding_lost", str(root))
        state.recheck_auth_file("auth_json_binding_lost")
        state.require_no_config_toml("config_toml_absence_recheck")
    if project_relpath is not None:
        state.require_no_project_config(
            "project_config_closure_recheck", effective_cwd, project_relpath
        )


def _digest_path(path: str) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        return _errno_name(exc)
    try:
        digest = hashlib.sha256()
        while True:
            block = os.read(fd, _HASH_BLOCK_BYTES)
            if not block:
                break
            digest.update(block)
        return digest.hexdigest()
    finally:
        os.close(fd)


def attest_spawn_boundary(
    *,
    expected: SealedRuntimeIdentity,
    launch: Any,
    fixed_env: Mapping[str, str],
    effective_cwd: str,
    run_dir: Path,
    ownership: TrustedOwnership,
) -> SpawnAttestation:
    """Attest the spawn boundary or refuse it fail-closed.

    ``attestation.json`` is written write-once in ``run_dir`` **before** any
    refusal is raised, on PASS and on FAIL, so a refused Run leaves the sealed
    identity (``launch.json``) and the observed one side by side.

    On PASS the returned :class:`SpawnAttestation` carries the still-open
    ``O_PATH`` pin of the image that will be exec'd; the caller owns closing
    it. Every other descriptor opened here is closed on every path.
    """
    state = _AttestationState()
    try:
        _perform(
            state,
            expected=expected,
            launch=launch,
            fixed_env=fixed_env,
            effective_cwd=effective_cwd,
            ownership=ownership,
        )
    except _CheckFailed as failure:
        state.checks.append(
            AttestationCheck(
                name=failure.name,
                expected=failure.expected,
                observed=failure.observed,
                passed=False,
            )
        )
        state.close_all()
        storage.write_once_json(
            Path(run_dir) / ATTESTATION_FILENAME, state.report(passed=False).to_dict()
        )
        raise _refusal_for(failure) from None
    except BaseException:
        state.close_all()
        raise

    exec_fd = state.detach("node" if expected.launch_kind == "wrapped_acp" else "cli")
    state.close_all()
    report = state.report(passed=True)
    try:
        storage.write_once_json(Path(run_dir) / ATTESTATION_FILENAME, report.to_dict())
    except BaseException:
        os.close(exec_fd)
        raise
    return SpawnAttestation(report=report, exec_fd=exec_fd)

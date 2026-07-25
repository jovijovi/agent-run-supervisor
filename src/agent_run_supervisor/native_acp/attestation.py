"""Spawn-boundary attestation for identity-pinned profiles (design D10).

A profile that freezes an :class:`ExpectedRuntimeIdentity` gets one
deterministic check point immediately before its child is spawned:

``pin → credential-alias refusal → hash-through-inode → credential-root
structure → project-config closure → race seam → liveness/absence recheck →
write-once report``

The gate is **detect-and-refuse at every Run**, not a runtime cage. It proves
that the artifacts about to be executed, the credential root about to be used,
and the configuration surfaces the agent will read are exactly the frozen ones
*at check time*, on both sides of the race window, and refuses fail-closed
otherwise. It never OS-sandboxes the launched runtime and makes no claim about
an actor racing the residual window inside a single spawn.

Artifacts are pinned as ``O_PATH`` descriptors and hashed by reopening those
descriptors through ``/proc/self/fd``, so identity is bound to inodes rather
than to pathnames. On PASS the Node pin survives as ``interpreter_fd`` and the
spawn exec's that descriptor directly, leaving the interpreter image with zero
residual swap window.

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

ATTESTATION_SCHEMA_VERSION = 1
ATTESTATION_FILENAME = "attestation.json"

# Refusal classes. Every check row maps to exactly one of them.
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
    "argv_node_binding": RUNTIME_IDENTITY_MISMATCH,
    "argv_adapter_entry_binding": RUNTIME_IDENTITY_MISMATCH,
    "env_cli_path_binding": RUNTIME_IDENTITY_MISMATCH,
    "node_binding_lost": RUNTIME_IDENTITY_MISMATCH,
    "adapter_entry_binding_lost": RUNTIME_IDENTITY_MISMATCH,
    "cli_binding_lost": RUNTIME_IDENTITY_MISMATCH,
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
_HASH_BLOCK_BYTES = 1 << 20
_CREDENTIAL_ALIAS_EXPECTED = "distinct from credential file"
_NOT_DECLARED = "not declared"

# Per-runtime bindings the gate needs but cannot infer. Their defaults are the
# Codex values that were frozen before any second identity-pinned profile
# existed; :meth:`ExpectedRuntimeIdentity.to_dict` omits each field at its
# default so the merged Codex snapshot, profile hash, and launch hash stay
# byte-identical while a second runtime declares its own bindings explicitly.
DEFAULT_CLI_PATH_ENV = "CODEX_PATH"
DEFAULT_CREDENTIAL_ROOT_ENV = "CODEX_HOME"
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
class ExpectedRuntimeIdentity:
    """Profile-frozen identity of the runtime a spawn is allowed to launch.

    The last three fields bind the gate to *this* runtime's surfaces: the
    fixed-env key that must carry the CLI path, the fixed-env key holding an
    ARS-managed credential root (``None`` when the downstream CLI owns its own
    credential storage), and the workspace project-config path whose ancestor
    chain must stay clean (``None`` when the profile freezes no such surface).
    A ``None`` surface never drops rows silently: the report records an
    explicit ``*_not_declared`` row instead.
    """

    node_path: str
    node_sha256: str
    adapter_entry_path: str
    adapter_entry_sha256: str
    cli_path: str
    cli_sha256: str
    agent_info_name: str
    agent_info_version: str
    protocol_version: str
    cli_path_env: str = DEFAULT_CLI_PATH_ENV
    credential_root_env: str | None = DEFAULT_CREDENTIAL_ROOT_ENV
    project_config_relpath: str | None = DEFAULT_PROJECT_CONFIG_RELPATH

    def __post_init__(self) -> None:
        for field_name in (
            "node_path",
            "node_sha256",
            "adapter_entry_path",
            "adapter_entry_sha256",
            "cli_path",
            "cli_sha256",
            "agent_info_name",
            "agent_info_version",
            "protocol_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"expected runtime identity {field_name} must be a non-empty string"
                )
        for field_name in ("node_path", "adapter_entry_path", "cli_path"):
            if not Path(getattr(self, field_name)).is_absolute():
                raise ValueError(
                    f"expected runtime identity {field_name} must be an absolute path"
                )
        for field_name in ("node_sha256", "adapter_entry_sha256", "cli_sha256"):
            value = getattr(self, field_name)
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise ValueError(
                    f"expected runtime identity {field_name} must be a sha256 hex digest"
                )
        if not self.protocol_version.isdigit():
            raise ValueError(
                "expected runtime identity protocol_version must be a decimal string"
            )
        for field_name in ("cli_path_env", "credential_root_env"):
            value = getattr(self, field_name)
            if field_name == "credential_root_env" and value is None:
                continue
            if not isinstance(value, str) or not value or "=" in value:
                raise ValueError(
                    f"expected runtime identity {field_name} must be an "
                    "environment variable name"
                )
        relpath = self.project_config_relpath
        if relpath is not None:
            if not isinstance(relpath, str) or not relpath:
                raise ValueError(
                    "expected runtime identity project_config_relpath must be a "
                    "non-empty relative path"
                )
            parts = Path(relpath).parts
            if Path(relpath).is_absolute() or not parts or ".." in parts:
                raise ValueError(
                    "expected runtime identity project_config_relpath must be a "
                    "relative path without parent references"
                )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "node_path": self.node_path,
            "node_sha256": self.node_sha256,
            "adapter_entry_path": self.adapter_entry_path,
            "adapter_entry_sha256": self.adapter_entry_sha256,
            "cli_path": self.cli_path,
            "cli_sha256": self.cli_sha256,
            "agent_info_name": self.agent_info_name,
            "agent_info_version": self.agent_info_version,
            "protocol_version": self.protocol_version,
        }
        # Omit-when-default: a runtime that keeps the pre-existing bindings
        # serializes exactly as it did before they were expressible.
        if self.cli_path_env != DEFAULT_CLI_PATH_ENV:
            payload["cli_path_env"] = self.cli_path_env
        if self.credential_root_env != DEFAULT_CREDENTIAL_ROOT_ENV:
            payload["credential_root_env"] = self.credential_root_env
        if self.project_config_relpath != DEFAULT_PROJECT_CONFIG_RELPATH:
            payload["project_config_relpath"] = self.project_config_relpath
        return payload


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
    report: AttestationReport
    interpreter_fd: int


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

    One pure predicate, two call sites per attestation — the pre-hook pass and
    the post-hook recheck cannot diverge.
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

    def pin_artifact(
        self, artifact: str, name: str, path: str, *, nofollow: bool
    ) -> None:
        """O_PATH pin of a regular file; the descriptor carries the identity.

        ``nofollow`` is False for the CLI only: its configured path is a
        symlink by design, so the pin resolves to the final target inode.
        """
        flags = os.O_PATH | os.O_CLOEXEC
        if nofollow:
            # O_PATH|O_NOFOLLOW pins the symlink itself rather than failing, so
            # the regular-file assertion below is what refuses a swapped link.
            flags |= os.O_NOFOLLOW
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

    def recheck_artifact(
        self, artifact: str, name: str, path: str, *, nofollow: bool
    ) -> None:
        flags = os.O_PATH | os.O_CLOEXEC
        if nofollow:
            flags |= os.O_NOFOLLOW
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


def _perform(
    state: _AttestationState,
    *,
    expected: ExpectedRuntimeIdentity,
    launch: Any,
    fixed_env: Mapping[str, str],
    effective_cwd: str,
) -> None:
    if not hasattr(os, "O_PATH"):
        # Never degrade to pathname trust: descriptor pinning is the gate.
        raise _CheckFailed("platform_unsupported", "os.O_PATH", "unavailable")

    # 1. Pin every artifact by descriptor before anything is measured.
    state.pin_artifact("node", "node_pin", expected.node_path, nofollow=True)
    state.pin_artifact(
        "adapter_entry", "adapter_entry_pin", expected.adapter_entry_path, nofollow=True
    )
    state.pin_artifact("cli", "cli_pin", expected.cli_path, nofollow=False)

    # 1b. Identify the declared credential root and auth.json structurally —
    #     stat facts only — and refuse any pinned artifact that resolves to
    #     that inode. This runs before step 2 so an aliased artifact is never
    #     read: hashing a credential file would both leak a credential-derived
    #     digest into the FAIL report and read bytes this gate must not touch.
    credential_env_key = expected.credential_root_env
    root = fixed_env.get(credential_env_key) if credential_env_key else None
    credential = _CredentialIdentity.identify(root)
    state.require_not_credential_alias("node", "node_credential_alias", credential)
    state.require_not_credential_alias(
        "adapter_entry", "adapter_entry_credential_alias", credential
    )
    state.require_not_credential_alias("cli", "cli_credential_alias", credential)

    # 2. Hash through the pinned inodes and bind argv/env to the same paths.
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
        "cli", "cli_sha256", expected.cli_sha256, credential=credential
    )
    argv = tuple(getattr(launch, "argv", ()) or ())
    state.equality(
        "argv_node_binding", expected.node_path, argv[0] if len(argv) > 0 else None
    )
    state.equality(
        "argv_adapter_entry_binding",
        expected.adapter_entry_path,
        argv[1] if len(argv) > 1 else None,
    )
    state.equality(
        "env_cli_path_binding",
        expected.cli_path,
        fixed_env.get(expected.cli_path_env),
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
    state.recheck_artifact("node", "node_binding_lost", expected.node_path, nofollow=True)
    state.recheck_artifact(
        "adapter_entry",
        "adapter_entry_binding_lost",
        expected.adapter_entry_path,
        nofollow=True,
    )
    state.recheck_artifact("cli", "cli_binding_lost", expected.cli_path, nofollow=False)
    if credential_env_key is not None:
        state.recheck_credential_root("credential_root_binding_lost", str(root))
        state.recheck_auth_file("auth_json_binding_lost")
        state.require_no_config_toml("config_toml_absence_recheck")
    if project_relpath is not None:
        state.require_no_project_config(
            "project_config_closure_recheck", effective_cwd, project_relpath
        )


def attest_spawn_boundary(
    *,
    expected: ExpectedRuntimeIdentity,
    launch: Any,
    fixed_env: Mapping[str, str],
    effective_cwd: str,
    run_dir: Path,
) -> SpawnAttestation:
    """Attest the spawn boundary or refuse it fail-closed.

    ``attestation.json`` is written write-once in ``run_dir`` **before** any
    refusal is raised, on PASS and on FAIL, so a refused Run leaves the
    expected identity (``launch.json``) and the observed one side by side.

    On PASS the returned :class:`SpawnAttestation` carries the still-open
    ``O_PATH`` pin of the interpreter; the caller owns closing it. Every other
    descriptor opened here is closed on every path.
    """
    state = _AttestationState()
    try:
        _perform(
            state,
            expected=expected,
            launch=launch,
            fixed_env=fixed_env,
            effective_cwd=effective_cwd,
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

    interpreter_fd = state.detach("node")
    state.close_all()
    report = state.report(passed=True)
    try:
        storage.write_once_json(Path(run_dir) / ATTESTATION_FILENAME, report.to_dict())
    except BaseException:
        os.close(interpreter_fd)
        raise
    return SpawnAttestation(report=report, interpreter_fd=interpreter_fd)

"""Synthetic operator-owned Runtime Binding roots for hermetic tests.

Every helper builds fake artifacts under ``tmp_path``. Nothing here touches a
real Binding root, a real CLI, a credential, a provider, or a daemon.

The installed CLIs on this host are owned by the service UID, so they can never
be trusted artifacts. Tests therefore declare a *fake* service UID that owns
nothing and trust the UID that actually owns ``tmp_path`` — which is exactly
the shape an operator-prepared, root-owned artifact root would have.

Fixture executables are scripts, so C5 requires their interpreter to be frozen
rather than implicit. Each fixture stages a private copy of the host POSIX
shell inside its own tree and names it in the shebang, which is both the honest
shape and the only one reachable through a non-symlinked ancestor chain.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any

from agent_run_supervisor.native_acp import runtime_binding as rb
from agent_run_supervisor.native_acp.profile import (
    SLOT_KIND_NATIVE_BINARY,
    SLOT_KIND_PACKAGE_TREE,
    AgentProfile,
)

FAKE_SERVICE_UID = 4_000_000_001


def ownership() -> rb.TrustedOwnership:
    return rb.TrustedOwnership(
        trusted_uids=frozenset({os.getuid()}), service_uid=FAKE_SERVICE_UID
    )


def canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def harden(leaf: Path) -> None:
    """Clear group/other write from every directory we own up to ``/``.

    ``tmp_path`` intermediates inherit the ambient umask, which on this host
    leaves them group-writable. The refusal under test is about the *Binding*
    root, not about pytest's scratch layout.
    """
    current = leaf if leaf.is_dir() else leaf.parent
    while True:
        info = current.stat()
        if info.st_uid != os.getuid():
            return  # /tmp and above: sticky, not ours to change
        mode = stat.S_IMODE(info.st_mode)
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            current.chmod(mode & ~(stat.S_IWGRP | stat.S_IWOTH))
        parent = current.parent
        if parent == current:
            return
        current = parent


def harden_tree(root: Path) -> None:
    """Clear group/other write from a whole staged artifact tree, then upward.

    Package closures are ownership-checked entry by entry, so a sibling file
    left group-writable by the ambient umask is a genuine refusal — the fixture
    stages the shape an immutable operator-owned root would have.
    """
    root = Path(root)
    for path in [root, *root.rglob("*")]:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            continue
        mode = stat.S_IMODE(info.st_mode)
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            path.chmod(mode & ~(stat.S_IWGRP | stat.S_IWOTH))
    harden(root)


def write_canonical(path: Path, payload: Any) -> Path:
    path.write_text(canonical(payload), encoding="utf-8")
    path.chmod(0o644)
    return path


def stage_interpreter(root: Path, *, name: str = "sh") -> Path:
    """A private copy of the host POSIX shell, staged inside the fixture tree.

    Real and runnable, and — unlike ``/bin/sh`` — reachable through a chain of
    directories this fixture owns and has hardened, which is exactly the shape
    an operator-prepared immutable artifact root has.
    """
    target = Path(root) / "artifacts" / "interp" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(os.path.realpath("/bin/sh"), target)
        target.chmod(0o755)
    harden_tree(target.parent)
    return target


_INTERPRETER_CANDIDATES = ("/usr/bin/python3", "/bin/python3", "/usr/local/bin/python3")


def trusted_system_interpreter() -> Path:
    """A real Python whose file *and ancestors* satisfy the C5 trust rule.

    The test runner's own interpreter is not usable here: a uv-managed Python
    lives under a group-writable chain, so attesting it would be attesting an
    artifact the service UID's group could rewrite. Rather than relaxing the
    rule for a fixture, the harness attests an interpreter that genuinely
    passes it — and skips, loudly, on a host where none does.
    """
    import pytest

    policy = ownership()
    for candidate in _INTERPRETER_CANDIDATES:
        resolved = Path(os.path.realpath(candidate))
        if not resolved.is_file():
            continue
        try:
            rb.check_ownership(os.lstat(resolved), policy, "interpreter")
            rb.check_ancestors(resolved, policy, "interpreter")
        except rb.BindingRefusal:
            continue
        return resolved
    pytest.skip("no operator-owned system interpreter is available on this host")


def make_native_binary(
    root: Path, *, name: str = "agent-cli", body: str | None = None
) -> Path:
    """A fake CLI whose shebang names the fixture's own staged interpreter."""
    artifacts = Path(root) / "artifacts" / "cli" / "bin"
    artifacts.mkdir(parents=True, exist_ok=True)
    target = artifacts / name
    if body is None:
        body = "#!" + str(stage_interpreter(root)) + "\nexit 0\n"
    target.write_text(body, encoding="utf-8")
    target.chmod(0o755)
    harden(artifacts)
    return target


def declared_interpreter(path: Path) -> Path | None:
    """Read back the interpreter a fixture artifact actually needs (C5)."""
    actual = rb.required_interpreter(Path(path), surface="fixture artifact")
    return None if actual is None else Path(actual)


def native_binary_slot(
    path: Path, *, version: str = "1.0.0", interpreter: Path | None = None
) -> dict[str, Any]:
    if interpreter is None:
        interpreter = declared_interpreter(path)
    return {
        "kind": SLOT_KIND_NATIVE_BINARY,
        "path": str(path),
        "version": version,
        "sha256": sha256_file(path),
        "interpreter": None if interpreter is None else str(interpreter),
        "interpreter_sha256": (
            None if interpreter is None else sha256_file(Path(interpreter))
        ),
    }


def make_package_tree(
    root: Path,
    *,
    version: str = "1.0.0",
    subdir: str = "downstream",
    reports_version: bool = False,
) -> dict[str, Any]:
    """A fake wrapped-CLI package closure under ``artifacts/<subdir>/<version>``.

    ``subdir`` keeps two profiles staged in the same fixture root from sharing
    one package tree. ``reports_version`` makes the launcher print ``version``,
    which is what the code-owned probe has to read back for a promotion.
    """
    package_root = Path(root) / "artifacts" / subdir / version
    lib = package_root / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "cli.js").write_text("// sibling code\n", encoding="utf-8")
    interpreter = Path(root) / "artifacts" / "node" / "bin" / "node"
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    if not interpreter.exists():
        shutil.copy2(os.path.realpath("/bin/sh"), interpreter)
        interpreter.chmod(0o755)
    launcher = package_root / "bin" / "downstream"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    # The launcher runs the interpreter the descriptor freezes; a shebang
    # naming anything else would leave the real runtime unfrozen (C5).
    body = f"printf '%s' '{version}'\n" if reports_version else "exit 0\n"
    launcher.write_text("#!" + str(interpreter) + "\n" + body, encoding="utf-8")
    launcher.chmod(0o755)
    harden_tree(package_root)
    harden_tree(interpreter.parent)
    return {
        "kind": SLOT_KIND_PACKAGE_TREE,
        "package_root": str(package_root),
        "tree_sha256": rb.package_tree_digest(package_root),
        "launcher_path": str(launcher),
        "launcher_sha256": sha256_file(launcher),
        "interpreter_path": str(interpreter),
        "interpreter_sha256": sha256_file(interpreter),
        "version": version,
    }


def make_config_root(root: Path, *, name: str = "config-root") -> dict[str, Any]:
    path = Path(root) / "artifacts" / name
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    harden(path.parent)
    return {"kind": "config_root", "path": str(path)}


def default_slots(
    profile: AgentProfile,
    root: Path,
    *,
    version: str = "1.0.0",
    reports_version: bool = False,
) -> dict[str, Any]:
    """A minimal admissible slot set for whatever the contract declares.

    Artifacts are namespaced by profile so several profiles can be staged into
    one fixture root without sharing an artifact tree.
    """
    slots: dict[str, Any] = {}
    for slot in profile.contract.binding_slots:
        staged = f"{profile.profile_id}-{slot.name}"
        if slot.kind == SLOT_KIND_NATIVE_BINARY:
            binary = make_native_binary(
                root,
                name=staged,
                body=(
                    "#!" + str(stage_interpreter(root)) + f"\nprintf '%s' '{version}'\n"
                    if reports_version
                    else None
                ),
            )
            slots[slot.name] = native_binary_slot(binary, version=version)
        elif slot.kind == SLOT_KIND_PACKAGE_TREE:
            slots[slot.name] = make_package_tree(
                root, version=version, subdir=staged, reports_version=reports_version
            )
        else:
            slots[slot.name] = make_config_root(root, name=staged)
    return slots


def build_binding_root(
    tmp_path: Path,
    profile: AgentProfile,
    *,
    slots: dict[str, Any] | None = None,
    generation_id: str = "gen-0001",
    epoch: int = 1,
    contract_identity: dict[str, Any] | None = None,
    manifest_overrides: dict[str, Any] | None = None,
    pointer_overrides: dict[str, Any] | None = None,
    write_pointer: bool = True,
    dirname: str = "binding-root",
    version: str = "1.0.0",
    reports_version: bool = False,
) -> Path:
    """One operator-shaped Binding root carrying one generation for ``profile``.

    Called repeatedly with the same ``dirname``, it stages several profiles into
    the same root — which is what a real deployment has, since one daemon takes
    one ``--binding-root`` and the registry is closed at three profiles.
    """
    root = Path(tmp_path) / dirname
    manifest_path = rb.generation_manifest_path(root, profile.profile_id, generation_id)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if slots is None:
        slots = default_slots(
            profile, root, version=version, reports_version=reports_version
        )
    manifest: dict[str, Any] = {
        "schema_version": rb.BINDING_SCHEMA_VERSION,
        "generation_id": generation_id,
        "contract_identity": contract_identity
        if contract_identity is not None
        else {
            "profile_id": profile.profile_id,
            "profile_revision": profile.revision,
            "adapter_contract_hash": profile.adapter_contract_hash(),
        },
        "slots": slots,
        "session_compatibility_epoch": epoch,
        "provenance": {
            "created_at": "2026-07-26T09:00:00+08:00",
            "accepted_by": "operator",
            "accepted_at": "2026-07-26T09:00:00+08:00",
            "acceptance_receipt": {"ref": "receipt:local", "sha256": "a" * 64},
        },
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    write_canonical(manifest_path, manifest)
    if write_pointer:
        pointer = {
            "schema_version": rb.BINDING_SCHEMA_VERSION,
            "profile_id": profile.profile_id,
            "generation_id": generation_id,
            "manifest_sha256": sha256_file(manifest_path),
        }
        if pointer_overrides:
            pointer.update(pointer_overrides)
        write_canonical(rb.active_pointer_path(root, profile.profile_id), pointer)
    profile_dir = rb.profile_binding_dir(root, profile.profile_id)
    for directory in (
        root,
        root / rb.PROFILES_DIRNAME,
        profile_dir,
        profile_dir / rb.GENERATIONS_DIRNAME,
        manifest_path.parent,
    ):
        directory.chmod(0o755)
    harden(manifest_path.parent)
    return root


def admitted(
    tmp_path: Path, profile: AgentProfile, **kwargs: Any
) -> rb.AdmittedRuntimeBinding:
    """The value ``arsd.admission`` would hand a RunTask for this profile."""
    root = build_binding_root(tmp_path, profile, **kwargs)
    policy = ownership()
    return rb.AdmittedRuntimeBinding(
        resolved=rb.BindingReader(root, ownership=policy).resolve_active(profile),
        ownership=policy,
    )

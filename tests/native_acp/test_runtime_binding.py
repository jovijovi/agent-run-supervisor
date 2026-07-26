"""PR-B WP2: source-frozen AdapterContract and the operator-owned Binding reader.

Every test here is hermetic: synthetic Binding roots and fake artifacts under
``tmp_path``. No real CLI, credential, provider, daemon, or Binding root is
touched, and no artifact is installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import time
from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor.native_acp import runtime_binding as rb
from agent_run_supervisor.native_acp.profile import (
    CLAUDE_AGENT_ACP_0_61_0,
    CODEX_ACP_1_1_7,
    DEFAULT_REGISTRY,
    LAUNCH_KIND_DIRECT,
    LAUNCH_KIND_WRAPPED,
    OPENCODE_NATIVE_ACP,
    SLOT_DESCRIPTOR_FIELDS,
    SLOT_KIND_CONFIG_ROOT,
    SLOT_KIND_NATIVE_BINARY,
    SLOT_KIND_PACKAGE_TREE,
)

# ---------------------------------------------------------------------------
# Synthetic operator-owned Binding roots
#
# The installed OpenCode executable on this host is owned by the service UID, so
# it can never be a trusted artifact. Tests therefore declare a *fake* service
# UID that owns nothing, and trust the UID that actually owns tmp_path.
# ---------------------------------------------------------------------------

FAKE_SERVICE_UID = 4_000_000_001


def ownership() -> rb.TrustedOwnership:
    return rb.TrustedOwnership(
        trusted_uids=frozenset({os.getuid()}), service_uid=FAKE_SERVICE_UID
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


from .binding_fixtures import (  # noqa: E402
    declared_interpreter,
    harden,
    harden_tree,
    stage_interpreter,
)


def write_canonical(path: Path, payload: Any) -> Path:
    path.write_text(canonical(payload), encoding="utf-8")
    path.chmod(0o644)
    return path


def _is_sha256_text(text: str) -> bool:
    """True when ``text`` contains a 64-character lowercase hex run."""
    import re

    return re.search(r"[0-9a-f]{64}", text) is not None


def make_native_binary(
    root: Path, *, name: str = "opencode", body: str | None = None
) -> Path:
    """A fake direct_acp CLI whose shebang names the fixture's interpreter."""
    artifacts = root / "artifacts" / "opencode" / "1.18.5" / "bin"
    artifacts.mkdir(parents=True, exist_ok=True)
    target = artifacts / name
    if body is None:
        body = "#!" + str(stage_interpreter(root)) + "\nexit 0\n"
    target.write_text(body, encoding="utf-8")
    target.chmod(0o755)
    harden(artifacts)
    return target


def native_binary_descriptor(
    path: Path, *, version: str = "1.18.5", interpreter: Path | None = None
) -> dict[str, Any]:
    if interpreter is None:
        interpreter = declared_interpreter(path)
    return {
        "path": str(path),
        "version": version,
        "sha256": sha256_file(path),
        "interpreter": None if interpreter is None else str(interpreter),
        "interpreter_sha256": (
            None if interpreter is None else sha256_file(Path(interpreter))
        ),
    }


def make_package_tree(root: Path, *, version: str = "0.60.0") -> dict[str, Any]:
    package_root = root / "artifacts" / "claude-cli" / version
    lib = package_root / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "cli.js").write_text("// sibling code\n", encoding="utf-8")
    interpreter = root / "artifacts" / "node" / "v24.14.0" / "bin" / "node"
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    if not interpreter.exists():
        import shutil

        shutil.copy2(os.path.realpath("/bin/sh"), interpreter)
        interpreter.chmod(0o755)
    launcher = package_root / "bin" / "claude"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    # The shebang names the interpreter the descriptor freezes: the declared
    # runtime and the real one are the same fact, not two hopeful ones (C5).
    launcher.write_text(
        "#!" + str(interpreter) + "\nexec lib/cli.js\n", encoding="utf-8"
    )
    launcher.chmod(0o755)
    harden_tree(package_root)
    harden_tree(interpreter.parent)
    return {
        "package_root": str(package_root),
        "tree_sha256": rb.package_tree_digest(package_root),
        "launcher_path": str(launcher),
        "launcher_sha256": sha256_file(launcher),
        "interpreter_path": str(interpreter),
        "interpreter_sha256": sha256_file(interpreter),
        "version": version,
    }


def build_root(
    tmp_path: Path,
    *,
    profile=OPENCODE_NATIVE_ACP,
    slots: dict[str, Any] | None = None,
    generation_id: str = "gen-0007",
    epoch: int = 3,
    contract_identity: dict[str, Any] | None = None,
    manifest_overrides: dict[str, Any] | None = None,
    pointer_overrides: dict[str, Any] | None = None,
    write_pointer: bool = True,
) -> Path:
    root = tmp_path / "binding-root"
    (root / rb.GENERATIONS_DIRNAME / generation_id).mkdir(parents=True, exist_ok=True)
    if slots is None:
        binary = make_native_binary(root)
        slots = {"agent_cli": {"kind": SLOT_KIND_NATIVE_BINARY, **native_binary_descriptor(binary)}}
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
    manifest_path = root / rb.GENERATIONS_DIRNAME / generation_id / rb.MANIFEST_FILENAME
    write_canonical(manifest_path, manifest)
    if write_pointer:
        pointer = {
            "schema_version": rb.BINDING_SCHEMA_VERSION,
            "generation_id": generation_id,
            "manifest_sha256": sha256_file(manifest_path),
        }
        if pointer_overrides:
            pointer.update(pointer_overrides)
        write_canonical(root / rb.ACTIVE_FILENAME, pointer)
    for directory in (root, root / rb.GENERATIONS_DIRNAME, manifest_path.parent):
        directory.chmod(0o755)
    harden(manifest_path.parent)
    return root


# ---------------------------------------------------------------------------
# C1 — the source-frozen AdapterContract
# ---------------------------------------------------------------------------


def test_every_registered_profile_carries_a_closed_adapter_contract() -> None:
    for profile_id in DEFAULT_REGISTRY.ids():
        profile = DEFAULT_REGISTRY.get(profile_id)
        contract = profile.contract
        assert contract.launch_kind in (LAUNCH_KIND_WRAPPED, LAUNCH_KIND_DIRECT)
        assert contract.acp_agent_name
        assert contract.acp_protocol_version == "1"
        assert contract.required_capabilities == ("loadSession",)
        # Forbidden capabilities are declarable for every contract but are
        # populated only where a real initialize exchange observed what the
        # agent advertises; nothing is forbidden on speculation.
        assert isinstance(contract.forbidden_capabilities, tuple)
        assert contract.version_probe.argv_suffix
        assert len(profile.adapter_contract_hash()) == 64
    assert OPENCODE_NATIVE_ACP.contract.forbidden_capabilities == ("terminal",)


def test_registered_contracts_declare_exactly_one_artifact_slot() -> None:
    expected = {
        "opencode-native-acp": (LAUNCH_KIND_DIRECT, SLOT_KIND_NATIVE_BINARY),
        "codex-acp-1.1.7": (LAUNCH_KIND_WRAPPED, SLOT_KIND_PACKAGE_TREE),
        "claude-agent-acp-0.61.0": (LAUNCH_KIND_WRAPPED, SLOT_KIND_PACKAGE_TREE),
    }
    for profile_id, (launch_kind, artifact_kind) in expected.items():
        contract = DEFAULT_REGISTRY.get(profile_id).contract
        assert contract.launch_kind == launch_kind
        artifact_slots = [
            slot
            for slot in contract.binding_slots
            if slot.kind in (SLOT_KIND_NATIVE_BINARY, SLOT_KIND_PACKAGE_TREE)
        ]
        assert len(artifact_slots) == 1
        assert artifact_slots[0].kind == artifact_kind
        assert contract.requires_binding is True


def test_no_registered_contract_carries_a_deployment_path_version_or_digest() -> None:
    """C1/C2 structural assertion: deployment facts left the source constants.

    The only paths and digests a contract may still carry are the *source*
    interpreter and ACP adapter artifacts of a wrapped profile (C9). No
    downstream CLI path, version, digest, or config-root value survives.
    """
    forbidden_substrings = (
        "/home/linuxbrew",
        "/home/ecs-user/.local/bin/codex",
        "/home/ecs-user/.local/bin/claude",
        "/home/ecs-user/.config/agent-run-supervisor",
    )
    for profile_id in DEFAULT_REGISTRY.ids():
        profile = DEFAULT_REGISTRY.get(profile_id)
        blob = canonical(profile.contract_snapshot())
        for needle in forbidden_substrings:
            assert needle not in blob, f"{profile_id} still freezes {needle}"
        for slot in profile.contract.binding_slots:
            # A slot declares a name/kind/binding key and the *shape* a
            # generation must fill — never a value.
            payload = slot.to_dict()
            assert set(payload) <= {
                "name",
                "kind",
                "env_key",
                "provides_executable",
                "descriptor_fields",
            }
            assert payload["descriptor_fields"] == list(
                SLOT_DESCRIPTOR_FIELDS[slot.kind]
            )
            # Field *names* may mention a digest; no digest value may appear.
            for value in payload["descriptor_fields"]:
                assert not _is_sha256_text(value)
            assert not _is_sha256_text(canonical(payload))


def test_adapter_contract_hash_changes_with_any_contract_change() -> None:
    import dataclasses

    baseline = OPENCODE_NATIVE_ACP.adapter_contract_hash()
    assert OPENCODE_NATIVE_ACP.adapter_contract_hash() == baseline  # stable
    bumped = dataclasses.replace(OPENCODE_NATIVE_ACP, revision=99)
    assert bumped.adapter_contract_hash() != baseline
    retuned = dataclasses.replace(
        OPENCODE_NATIVE_ACP,
        contract=dataclasses.replace(
            OPENCODE_NATIVE_ACP.contract, forbidden_capabilities=()
        ),
    )
    assert retuned.adapter_contract_hash() != baseline


# ---------------------------------------------------------------------------
# C7 — layout, canonical JSON, and the refusal matrix
# ---------------------------------------------------------------------------


def _resolve(root: Path, profile=OPENCODE_NATIVE_ACP) -> rb.ResolvedBinding:
    return rb.BindingReader(root, ownership=ownership()).resolve_active(profile)


def test_valid_generation_resolves_with_projected_slots_and_hashes(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    resolved = _resolve(root)
    assert resolved.generation_id == "gen-0007"
    assert resolved.session_compatibility_epoch == 3
    assert set(resolved.slots) == {"agent_cli"}
    assert resolved.slots["agent_cli"].kind == SLOT_KIND_NATIVE_BINARY
    assert len(resolved.slot_set_hash) == 64
    assert len(resolved.generation_hash) == 64
    assert resolved.contract_identity["profile_id"] == "opencode-native-acp"
    # Provenance is recorded and reported, never consulted.
    assert resolved.provenance["accepted_by"] == "operator"
    assert resolved.acceptance_receipt_ref == "receipt:local"


def test_slot_and_set_hashes_change_with_the_descriptor(tmp_path: Path) -> None:
    first = _resolve(build_root(tmp_path / "a"))
    second_root = build_root(tmp_path / "b")
    binary = make_native_binary(
        second_root,
        body="#!" + str(stage_interpreter(second_root)) + "\n# different\n",
    )
    second_root_final = build_root(
        tmp_path / "c",
        slots={
            "agent_cli": {
                "kind": SLOT_KIND_NATIVE_BINARY,
                **native_binary_descriptor(binary),
            }
        },
    )
    assert second_root.exists()
    second = _resolve(second_root_final)
    assert first.slots["agent_cli"].slot_hash != second.slots["agent_cli"].slot_hash
    assert first.slot_set_hash != second.slot_set_hash


@pytest.mark.parametrize(
    "rule,mutate",
    [
        ("UNKNOWN_MANIFEST_FIELD", {"manifest_overrides": {"extra": 1}}),
        ("SCHEMA_VERSION", {"manifest_overrides": {"schema_version": 2}}),
        ("EPOCH_NOT_POSITIVE", {"epoch": 0}),
        ("EPOCH_NOT_POSITIVE", {"epoch": -1}),
        (
            "CONTRACT_IDENTITY_MISMATCH",
            {"contract_identity": {
                "profile_id": "codex-acp-1.1.7",
                "profile_revision": 2,
                "adapter_contract_hash": "0" * 64,
            }},
        ),
        ("GENERATION_ID_MISMATCH", {"manifest_overrides": {"generation_id": "gen-9999"}}),
    ],
)
def test_manifest_refusal_matrix(tmp_path: Path, rule: str, mutate: dict[str, Any]) -> None:
    root = build_root(tmp_path, **mutate)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == rule


def test_missing_contract_identity_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path, manifest_overrides={"contract_identity": None})
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule in ("CONTRACT_IDENTITY_ABSENT", "MANIFEST_FIELD_TYPE")


def test_unknown_slot_name_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    binary = make_native_binary(root)
    root = build_root(
        tmp_path / "two",
        slots={
            "agent_cli": {
                "kind": SLOT_KIND_NATIVE_BINARY,
                **native_binary_descriptor(binary),
            },
            "smuggled_cli": {
                "kind": SLOT_KIND_NATIVE_BINARY,
                **native_binary_descriptor(binary),
            },
        },
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "UNKNOWN_SLOT"


def test_slot_kind_mismatch_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    binary = make_native_binary(root)
    root = build_root(
        tmp_path / "kind",
        slots={
            "agent_cli": {
                "kind": SLOT_KIND_CONFIG_ROOT,
                "path": str(binary.parent),
            }
        },
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "SLOT_KIND_MISMATCH"


def test_slot_missing_a_required_descriptor_field_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    binary = make_native_binary(root)
    descriptor = native_binary_descriptor(binary)
    descriptor.pop("sha256")
    root = build_root(
        tmp_path / "missing",
        slots={"agent_cli": {"kind": SLOT_KIND_NATIVE_BINARY, **descriptor}},
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "SLOT_DESCRIPTOR_FIELDS"


def test_slot_with_an_unknown_descriptor_field_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    binary = make_native_binary(root)
    root = build_root(
        tmp_path / "extra",
        slots={
            "agent_cli": {
                "kind": SLOT_KIND_NATIVE_BINARY,
                **native_binary_descriptor(binary),
                "argv": ["--danger"],
            }
        },
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "SLOT_DESCRIPTOR_FIELDS"


def test_launcher_only_package_tree_is_refused(tmp_path: Path) -> None:
    """C5 encoded directly: a launcher hash never freezes its sibling code."""
    root = tmp_path / "wrapped"
    descriptor = make_package_tree(root)
    descriptor.pop("tree_sha256")
    descriptor.pop("interpreter_sha256")
    built = build_root(
        tmp_path,
        profile=CLAUDE_AGENT_ACP_0_61_0,
        slots={"downstream_cli": {"kind": SLOT_KIND_PACKAGE_TREE, **descriptor}},
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(built, CLAUDE_AGENT_ACP_0_61_0)
    assert excinfo.value.rule == "SLOT_DESCRIPTOR_FIELDS"


def test_non_canonical_json_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    manifest_path = root / rb.GENERATIONS_DIRNAME / "gen-0007" / rb.MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_canonical(
        root / rb.ACTIVE_FILENAME,
        {
            "schema_version": rb.BINDING_SCHEMA_VERSION,
            "generation_id": "gen-0007",
            "manifest_sha256": sha256_file(manifest_path),
        },
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "NON_CANONICAL_JSON"


def test_oversize_file_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    manifest_path = root / rb.GENERATIONS_DIRNAME / "gen-0007" / rb.MANIFEST_FILENAME
    manifest_path.write_text("x" * (rb.MAX_BINDING_FILE_BYTES + 1), encoding="utf-8")
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "FILE_TOO_LARGE"


def test_manifest_digest_mismatch_against_the_pointer_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path, pointer_overrides={"manifest_sha256": "b" * 64})
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "MANIFEST_DIGEST_MISMATCH"


@pytest.mark.parametrize("generation_id", ["../escape", "..", "a/b", "", "gen 7", "/abs"])
def test_pointer_generation_id_traversal_is_refused(
    tmp_path: Path, generation_id: str
) -> None:
    root = build_root(tmp_path)
    write_canonical(
        root / rb.ACTIVE_FILENAME,
        {
            "schema_version": rb.BINDING_SCHEMA_VERSION,
            "generation_id": generation_id,
            "manifest_sha256": "c" * 64,
        },
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "GENERATION_ID_UNSAFE"


def test_symlinked_active_pointer_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    pointer = root / rb.ACTIVE_FILENAME
    real = root / "real-active.json"
    pointer.rename(real)
    pointer.symlink_to(real)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "NOT_A_REGULAR_FILE"


def test_symlinked_manifest_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    manifest_path = root / rb.GENERATIONS_DIRNAME / "gen-0007" / rb.MANIFEST_FILENAME
    real = manifest_path.parent / "real-manifest.json"
    manifest_path.rename(real)
    manifest_path.symlink_to(real)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "NOT_A_REGULAR_FILE"


def test_symlinked_generation_directory_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    generations = root / rb.GENERATIONS_DIRNAME
    real = generations / "real-gen"
    (generations / "gen-0007").rename(real)
    (generations / "gen-0007").symlink_to(real)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "NOT_A_DIRECTORY"


def test_fifo_where_a_regular_file_is_required_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    pointer = root / rb.ACTIVE_FILENAME
    pointer.unlink()
    os.mkfifo(pointer, 0o644)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule in ("NOT_A_REGULAR_FILE", "OPEN_FAILED")


def test_directory_where_a_regular_file_is_required_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    pointer = root / rb.ACTIVE_FILENAME
    pointer.unlink()
    pointer.mkdir()
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "NOT_A_REGULAR_FILE"


# ---------------------------------------------------------------------------
# C5 — artifact ownership, ancestors, and code closure
# ---------------------------------------------------------------------------


def test_artifact_owned_outside_the_trusted_set_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    reader = rb.BindingReader(
        root,
        ownership=rb.TrustedOwnership(
            trusted_uids=frozenset({FAKE_SERVICE_UID + 7}),
            service_uid=FAKE_SERVICE_UID,
        ),
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        reader.resolve_active(OPENCODE_NATIVE_ACP)
    assert excinfo.value.rule == "UNTRUSTED_OWNER"


def test_artifact_owned_by_the_service_uid_is_refused(tmp_path: Path) -> None:
    """Same-UID ownership is writability; the installed host CLI fails here.

    This is exactly why the OpenCode executable installed on this host cannot
    back a real promotion: the service UID owns it, so it can rewrite it.
    """
    root = build_root(tmp_path)
    reader = rb.BindingReader(
        root,
        ownership=rb.TrustedOwnership(
            trusted_uids=frozenset({0}), service_uid=os.getuid()
        ),
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        reader.resolve_active(OPENCODE_NATIVE_ACP)
    assert excinfo.value.rule == "SERVICE_UID_WRITABLE"


def test_trusted_ownership_refuses_a_service_uid_inside_the_trusted_set() -> None:
    with pytest.raises(ValueError):
        rb.TrustedOwnership(trusted_uids=frozenset({1234}), service_uid=1234)


def test_group_or_other_writable_artifact_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    binary = Path(
        json.loads(
            (root / rb.GENERATIONS_DIRNAME / "gen-0007" / rb.MANIFEST_FILENAME).read_text()
        )["slots"]["agent_cli"]["path"]
    )
    binary.chmod(0o777)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "GROUP_OR_OTHER_WRITABLE"


def test_group_or_other_writable_ancestor_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    binary = Path(
        json.loads(
            (root / rb.GENERATIONS_DIRNAME / "gen-0007" / rb.MANIFEST_FILENAME).read_text()
        )["slots"]["agent_cli"]["path"]
    )
    binary.parent.chmod(0o777)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "GROUP_OR_OTHER_WRITABLE"


def test_artifact_digest_mismatch_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    manifest_path = root / rb.GENERATIONS_DIRNAME / "gen-0007" / rb.MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    binary = Path(payload["slots"]["agent_cli"]["path"])
    binary.write_text("#!/bin/sh\n# swapped\n", encoding="utf-8")
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "ARTIFACT_DIGEST_MISMATCH"


def test_symlinked_artifact_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    manifest_path = root / rb.GENERATIONS_DIRNAME / "gen-0007" / rb.MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    binary = Path(payload["slots"]["agent_cli"]["path"])
    real = binary.with_name("real-opencode")
    binary.rename(real)
    binary.symlink_to(real)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "NOT_A_REGULAR_FILE"


def test_relative_artifact_path_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    binary = make_native_binary(root)
    built = build_root(
        tmp_path / "rel",
        slots={
            "agent_cli": {
                "kind": SLOT_KIND_NATIVE_BINARY,
                **{**native_binary_descriptor(binary), "path": "../../etc/passwd"},
            }
        },
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(built)
    assert excinfo.value.rule == "ARTIFACT_PATH_UNSAFE"


def test_wrapped_package_tree_resolves_and_refuses_a_writable_package_root(
    tmp_path: Path,
) -> None:
    descriptor = make_package_tree(tmp_path / "wrapped")
    built = build_root(
        tmp_path,
        profile=CLAUDE_AGENT_ACP_0_61_0,
        slots={"downstream_cli": {"kind": SLOT_KIND_PACKAGE_TREE, **descriptor}},
    )
    resolved = _resolve(built, CLAUDE_AGENT_ACP_0_61_0)
    assert resolved.slots["downstream_cli"].kind == SLOT_KIND_PACKAGE_TREE

    Path(descriptor["package_root"]).chmod(0o777)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(built, CLAUDE_AGENT_ACP_0_61_0)
    assert excinfo.value.rule == "GROUP_OR_OTHER_WRITABLE"


def test_writable_sibling_code_inside_the_closure_is_refused(tmp_path: Path) -> None:
    """A digest freezes bytes only until someone who can write them changes."""
    descriptor = make_package_tree(tmp_path / "wrapped")
    built = build_root(
        tmp_path,
        profile=CLAUDE_AGENT_ACP_0_61_0,
        slots={"downstream_cli": {"kind": SLOT_KIND_PACKAGE_TREE, **descriptor}},
    )
    (Path(descriptor["package_root"]) / "lib" / "cli.js").chmod(0o666)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(built, CLAUDE_AGENT_ACP_0_61_0)
    assert excinfo.value.rule == "GROUP_OR_OTHER_WRITABLE"


def test_sticky_world_writable_package_root_is_refused(tmp_path: Path) -> None:
    """C5/C10: sticky stops *removal*, never *addition* — closures need both.

    A ``01777`` package root is trusted-owned and its tree digest still matches
    byte for byte, so every other rule passes. The service/AGENT UID can
    nonetheless create a new entry inside it after the last recheck and before
    the wrapped adapter reopens the tree — sibling code no digest ever froze.
    Sticky semantics say nothing about creating entries, so they cannot make a
    code closure immutable.
    """
    descriptor = make_package_tree(tmp_path / "wrapped")
    built = build_root(
        tmp_path,
        profile=CLAUDE_AGENT_ACP_0_61_0,
        slots={"downstream_cli": {"kind": SLOT_KIND_PACKAGE_TREE, **descriptor}},
    )
    package_root = Path(descriptor["package_root"])
    package_root.chmod(0o1777)
    # Nothing about the closure's *contents* changed: the digest still matches,
    # so the digest alone cannot be what refuses this.
    assert rb.package_tree_digest(package_root) == descriptor["tree_sha256"]
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(built, CLAUDE_AGENT_ACP_0_61_0)
    assert excinfo.value.rule == "GROUP_OR_OTHER_WRITABLE"


def test_sticky_world_writable_directory_inside_the_closure_is_refused(
    tmp_path: Path,
) -> None:
    """The same rule one level in: ``lib/`` is where the loaded code lives."""
    descriptor = make_package_tree(tmp_path / "wrapped")
    built = build_root(
        tmp_path,
        profile=CLAUDE_AGENT_ACP_0_61_0,
        slots={"downstream_cli": {"kind": SLOT_KIND_PACKAGE_TREE, **descriptor}},
    )
    package_root = Path(descriptor["package_root"])
    (package_root / "lib").chmod(0o1777)
    assert rb.package_tree_digest(package_root) == descriptor["tree_sha256"]
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(built, CLAUDE_AGENT_ACP_0_61_0)
    assert excinfo.value.rule == "GROUP_OR_OTHER_WRITABLE"


def test_sticky_world_writable_binding_root_is_refused(tmp_path: Path) -> None:
    """The Binding root is a protected object, not an ambient ancestor."""
    root = build_root(tmp_path)
    root.chmod(0o1777)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "GROUP_OR_OTHER_WRITABLE"


def test_sticky_world_writable_generation_directory_is_refused(tmp_path: Path) -> None:
    """A generation directory holds manifest bytes, so it is protected too."""
    root = build_root(tmp_path)
    (root / rb.GENERATIONS_DIRNAME / "gen-0007").chmod(0o1777)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "GROUP_OR_OTHER_WRITABLE"


def test_sticky_ambient_ancestor_above_the_root_stays_admissible(tmp_path: Path) -> None:
    """The ``/tmp`` shape the hermetic suite runs under must keep working.

    An ancestor *above* the Binding root holds no Binding or closure content.
    Its sticky bit is exactly what stops a non-owner from renaming or removing
    the trusted-owned directory this walk selects, and the walk is dirfd-relative
    throughout — the entry that was proven is the entry that is used, so there is
    no pathname to re-resolve and nothing to race. That is a genuine guarantee
    about the selected child, which is why this one case is not the closure case.
    """
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    root = build_root(ambient)
    # After the fixture hardened its own chain, so the ancestor keeps this mode.
    ambient.chmod(0o1777)
    assert stat.S_IMODE(ambient.stat().st_mode) == 0o1777
    resolved = _resolve(root)
    assert resolved.generation_id == "gen-0007"


def test_package_tree_digest_change_is_refused(tmp_path: Path) -> None:
    descriptor = make_package_tree(tmp_path / "wrapped")
    built = build_root(
        tmp_path,
        profile=CLAUDE_AGENT_ACP_0_61_0,
        slots={"downstream_cli": {"kind": SLOT_KIND_PACKAGE_TREE, **descriptor}},
    )
    sibling = Path(descriptor["package_root"]) / "lib" / "cli.js"
    mode = sibling.stat().st_mode
    sibling.write_text("// swapped sibling code\n", encoding="utf-8")
    sibling.chmod(mode)  # only the bytes changed, not the permissions
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(built, CLAUDE_AGENT_ACP_0_61_0)
    assert excinfo.value.rule == "PACKAGE_TREE_DIGEST_MISMATCH"


def test_package_tree_digest_is_deterministic_and_order_independent(tmp_path: Path) -> None:
    descriptor = make_package_tree(tmp_path / "wrapped")
    root = Path(descriptor["package_root"])
    first = rb.package_tree_digest(root)
    (root / "zzz").mkdir()
    (root / "zzz" / "late.js").write_text("x\n", encoding="utf-8")
    second = rb.package_tree_digest(root)
    assert first != second
    assert rb.package_tree_digest(root) == second


def test_package_tree_digest_refuses_rather_than_samples(tmp_path: Path) -> None:
    root = tmp_path / "huge"
    root.mkdir()
    for index in range(5):
        (root / f"f{index}").write_text("x", encoding="utf-8")
    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.package_tree_digest(root, max_entries=2)
    assert excinfo.value.rule == "PACKAGE_TREE_TOO_LARGE"


# ---------------------------------------------------------------------------
# C3 — acceptance identity and stale generations
# ---------------------------------------------------------------------------


def test_contract_revision_bump_fails_every_prior_generation_closed(
    tmp_path: Path,
) -> None:
    import dataclasses

    root = build_root(tmp_path)
    reader = rb.BindingReader(root, ownership=ownership())
    assert reader.resolve_active(OPENCODE_NATIVE_ACP).generation_id == "gen-0007"
    revised = dataclasses.replace(OPENCODE_NATIVE_ACP, revision=99)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.BindingReader(root, ownership=ownership()).resolve_active(revised)
    assert excinfo.value.rule == "CONTRACT_IDENTITY_MISMATCH"


def test_valid_receipt_never_rescues_a_mismatched_contract_identity(
    tmp_path: Path,
) -> None:
    """Provenance is recorded and reported, never consulted."""
    root = build_root(
        tmp_path,
        contract_identity={
            "profile_id": "opencode-native-acp",
            "profile_revision": OPENCODE_NATIVE_ACP.revision,
            "adapter_contract_hash": "f" * 64,
        },
        manifest_overrides={
            "provenance": {
                "created_at": "2026-07-26T09:00:00+08:00",
                "accepted_by": "operator",
                "accepted_at": "2026-07-26T09:00:00+08:00",
                "acceptance_receipt": {"ref": "receipt:perfect", "sha256": "e" * 64},
            }
        },
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "CONTRACT_IDENTITY_MISMATCH"


def test_provenance_only_changes_never_make_an_invalid_generation_admissible(
    tmp_path: Path,
) -> None:
    root = build_root(
        tmp_path,
        epoch=0,
        manifest_overrides={
            "provenance": {
                "created_at": "2026-07-26T09:00:00+08:00",
                "accepted_by": "root",
                "accepted_at": "2026-07-26T09:00:00+08:00",
                "acceptance_receipt": {"ref": "receipt:root", "sha256": "d" * 64},
            }
        },
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "EPOCH_NOT_POSITIVE"


# ---------------------------------------------------------------------------
# C8 — read-once instrumentation
# ---------------------------------------------------------------------------


def test_resolve_active_reads_the_pointer_once_and_one_generation_once(
    tmp_path: Path,
) -> None:
    root = build_root(tmp_path)
    rb.reset_read_counters()
    _resolve(root)
    assert rb.read_counters() == {"active": 1, "generation": 1}


def test_a_second_resolution_is_a_second_pair_of_reads(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    rb.reset_read_counters()
    _resolve(root)
    _resolve(root)
    assert rb.read_counters() == {"active": 2, "generation": 2}


# ---------------------------------------------------------------------------
# C6 — the code-owned version probe
# ---------------------------------------------------------------------------


def _probe_script(tmp_path: Path, output: str) -> Path:
    script = tmp_path / "probe-cli"
    script.write_text(f"#!/bin/sh\nprintf '%s' '{output}'\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def test_probe_reads_the_real_version_through_the_code_owned_rule(tmp_path: Path) -> None:
    script = _probe_script(tmp_path, "1.18.5")
    observed = rb.probe_cli_version(
        executable=str(script), rule=OPENCODE_NATIVE_ACP.contract.version_probe
    )
    assert observed == "1.18.5"


def test_probe_parses_a_decorated_version_line(tmp_path: Path) -> None:
    script = _probe_script(tmp_path, "opencode 1.18.5 (linux-x64)")
    observed = rb.probe_cli_version(
        executable=str(script), rule=OPENCODE_NATIVE_ACP.contract.version_probe
    )
    assert observed == "1.18.5"


def test_probe_refuses_unparsable_output(tmp_path: Path) -> None:
    script = _probe_script(tmp_path, "no version here")
    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.probe_cli_version(
            executable=str(script), rule=OPENCODE_NATIVE_ACP.contract.version_probe
        )
    assert excinfo.value.rule == "PROBE_UNPARSABLE"


def test_probe_environment_carries_no_ambient_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leaked ambient variable would change the reported version."""
    script = tmp_path / "env-probe"
    script.write_text(
        '#!/bin/sh\nif [ -n "${SECRET_TOKEN}" ] || [ -n "${HOME}" ]; then\n'
        "  printf '%s' '9.9.9'\nelse\n  printf '%s' '1.2.3'\nfi\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("SECRET_TOKEN", "leaked")
    observed = rb.probe_cli_version(
        executable=str(script), rule=OPENCODE_NATIVE_ACP.contract.version_probe
    )
    assert observed == "1.2.3"


def test_validate_generation_refuses_a_probe_versus_manifest_mismatch(
    tmp_path: Path,
) -> None:
    root = build_root(tmp_path)
    manifest_path = root / rb.GENERATIONS_DIRNAME / "gen-0007" / rb.MANIFEST_FILENAME
    binary = Path(json.loads(manifest_path.read_text())["slots"]["agent_cli"]["path"])
    binary.write_text(
        "#!" + str(stage_interpreter(root)) + "\nprintf '%s' '9.9.9'\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    rebuilt = build_root(
        tmp_path / "probe",
        slots={
            "agent_cli": {
                "kind": SLOT_KIND_NATIVE_BINARY,
                **native_binary_descriptor(binary, version="1.18.5"),
            }
        },
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.validate_generation(
            rebuilt,
            "gen-0007",
            profile=OPENCODE_NATIVE_ACP,
            ownership=ownership(),
            probe=True,
        )
    assert excinfo.value.rule == "PROBE_VERSION_MISMATCH"


def test_validate_generation_accepts_a_matching_probe(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    binary = make_native_binary(
        scratch,
        body="#!" + str(stage_interpreter(scratch)) + "\nprintf '%s' '1.18.5'\n",
    )
    root = build_root(
        tmp_path,
        slots={
            "agent_cli": {
                "kind": SLOT_KIND_NATIVE_BINARY,
                **native_binary_descriptor(binary, version="1.18.5"),
            }
        },
    )
    resolved = rb.validate_generation(
        root,
        "gen-0007",
        profile=OPENCODE_NATIVE_ACP,
        ownership=ownership(),
        probe=True,
    )
    assert resolved.generation_id == "gen-0007"


# ---------------------------------------------------------------------------
# C7/C14 — the atomic active pointer
# ---------------------------------------------------------------------------


def test_write_active_pointer_replaces_atomically_and_never_creates_a_symlink(
    tmp_path: Path,
) -> None:
    root = build_root(tmp_path, generation_id="gen-0008", write_pointer=False)
    resolved = rb.validate_generation(
        root, "gen-0008", profile=OPENCODE_NATIVE_ACP, ownership=ownership(), probe=False
    )
    written = rb.write_active_pointer(root, resolved, ownership=ownership())
    assert written == root / rb.ACTIVE_FILENAME
    assert not written.is_symlink()
    assert stat.S_ISREG(os.lstat(written).st_mode)
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": rb.BINDING_SCHEMA_VERSION,
        "generation_id": "gen-0008",
        "manifest_sha256": resolved.manifest_sha256,
    }
    assert _resolve(root).generation_id == "gen-0008"


def test_binding_root_is_never_created_by_the_reader(tmp_path: Path) -> None:
    missing = tmp_path / "absent-root"
    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.BindingReader(missing, ownership=ownership()).resolve_active(
            OPENCODE_NATIVE_ACP
        )
    assert excinfo.value.rule in ("OPEN_FAILED", "NOT_A_DIRECTORY")
    assert not missing.exists()


# ---------------------------------------------------------------------------
# C7 — the Binding root's own ancestors are part of the trusted chain
#
# ``O_NOFOLLOW`` on the final component proves only that the leaf is not a
# symlink. An untrusted ancestor redirects or exposes every open beneath it —
# the pointer read, the generation read, and the operator's pointer write.
# ---------------------------------------------------------------------------


def test_symlinked_binding_root_ancestor_is_refused(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    build_root(actual)
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)

    reader = rb.BindingReader(alias / "binding-root", ownership=ownership())
    with pytest.raises(rb.BindingRefusal) as excinfo:
        reader.resolve_active(OPENCODE_NATIVE_ACP)
    assert excinfo.value.rule == "SYMLINKED_ANCESTOR"


def test_symlinked_binding_root_ancestor_is_refused_before_a_generation_read(
    tmp_path: Path,
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    build_root(actual)
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)

    reader = rb.BindingReader(alias / "binding-root", ownership=ownership())
    with pytest.raises(rb.BindingRefusal) as excinfo:
        reader.read_generation("gen-0007", profile=OPENCODE_NATIVE_ACP)
    assert excinfo.value.rule == "SYMLINKED_ANCESTOR"


def test_group_writable_binding_root_ancestor_is_refused(tmp_path: Path) -> None:
    # The artifact is staged outside the loose directory, so only the Binding
    # root's own ancestor walk can see that the root became a rename target.
    stage = tmp_path / "stage"
    binary = make_native_binary(stage)
    loose = tmp_path / "loose"
    loose.mkdir()
    root = build_root(
        loose,
        slots={
            "agent_cli": {
                "kind": SLOT_KIND_NATIVE_BINARY,
                **native_binary_descriptor(binary),
            }
        },
    )
    loose.chmod(0o777)  # non-sticky and group/other-writable: a rename target

    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.BindingReader(root, ownership=ownership()).resolve_active(OPENCODE_NATIVE_ACP)
    assert excinfo.value.rule == "GROUP_OR_OTHER_WRITABLE"


# ---------------------------------------------------------------------------
# C5/C10 — the package closure is one structurally bound executable closure
# ---------------------------------------------------------------------------


def _resolve_wrapped(tmp_path: Path, descriptor: dict[str, Any]):
    built = build_root(
        tmp_path,
        profile=CLAUDE_AGENT_ACP_0_61_0,
        slots={"downstream_cli": {"kind": SLOT_KIND_PACKAGE_TREE, **descriptor}},
    )
    return _resolve(built, CLAUDE_AGENT_ACP_0_61_0)


def _resolve_native(tmp_path: Path, descriptor: dict[str, Any]):
    built = build_root(
        tmp_path,
        slots={"agent_cli": {"kind": SLOT_KIND_NATIVE_BINARY, **descriptor}},
    )
    return _resolve(built)


def test_launcher_outside_the_hashed_package_root_is_refused(tmp_path: Path) -> None:
    """A launcher outside the tree loads unhashed sibling code (C5)."""
    stage = tmp_path / "wrapped"
    descriptor = make_package_tree(stage)
    outside = stage / "artifacts" / "loose" / "claude"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(Path(descriptor["launcher_path"]).read_bytes())
    outside.chmod(0o755)
    harden_tree(outside.parent)
    descriptor["launcher_path"] = str(outside)
    descriptor["launcher_sha256"] = sha256_file(outside)

    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve_wrapped(tmp_path, descriptor)
    assert excinfo.value.rule == "LAUNCHER_OUTSIDE_PACKAGE_ROOT"


def test_launcher_whose_real_runtime_is_not_the_declared_interpreter_is_refused(
    tmp_path: Path,
) -> None:
    """Freezing a Node digest proves nothing if the launcher runs a shell."""
    stage = tmp_path / "wrapped"
    descriptor = make_package_tree(stage)
    launcher = Path(descriptor["launcher_path"])
    other = stage / "artifacts" / "other-runtime"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    other.chmod(0o755)
    harden_tree(other.parent)
    launcher.write_text(f"#!{other}\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    harden_tree(Path(descriptor["package_root"]))
    descriptor["launcher_sha256"] = sha256_file(launcher)
    descriptor["tree_sha256"] = rb.package_tree_digest(Path(descriptor["package_root"]))

    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve_wrapped(tmp_path, descriptor)
    assert excinfo.value.rule == "INTERPRETER_MISMATCH"


# ---------------------------------------------------------------------------
# C5/C10 — a direct_acp executable's implicit interpreter/loader
# ---------------------------------------------------------------------------


def _elf(path: Path, *, interpreter: str | None) -> Path:
    """A minimal, non-executed ELF64 image used only for header inspection.

    Hermetic by construction: no compiler, no host binary, no execution. The
    only fields that matter are the ones the interpreter policy reads.
    """
    import struct

    phnum = 1 if interpreter is not None else 0
    ehsize, phentsize = 64, 56
    interp_offset = ehsize + phentsize * phnum
    header = (
        b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8
        + struct.pack(
            "<HHIQQQIHHHHHH",
            2,  # e_type = ET_EXEC
            0x3E,  # e_machine
            1,  # e_version
            0,  # e_entry
            ehsize if phnum else 0,  # e_phoff
            0,  # e_shoff
            0,  # e_flags
            ehsize,
            phentsize,
            phnum,
            64,
            0,
            0,
        )
    )
    body = b""
    if interpreter is not None:
        encoded = interpreter.encode("utf-8") + b"\x00"
        body = struct.pack(
            "<IIQQQQQQ",
            3,  # p_type = PT_INTERP
            4,  # p_flags = PF_R
            interp_offset,
            0,
            0,
            len(encoded),
            len(encoded),
            1,
        ) + encoded
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + body)
    path.chmod(0o755)
    return path


def _undeclared(descriptor: dict[str, Any]) -> dict[str, Any]:
    """The pre-repair descriptor shape: an artifact digest and nothing else."""
    return {**descriptor, "interpreter": None, "interpreter_sha256": None}


def test_native_binary_script_with_a_null_interpreter_is_refused(
    tmp_path: Path,
) -> None:
    """Hashing a script never freezes the interpreter the kernel selects."""
    root = tmp_path / "stage"
    binary = make_native_binary(root, body="#!/bin/sh\nexit 0\n")
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve_native(tmp_path, _undeclared(native_binary_descriptor(binary)))
    assert excinfo.value.rule == "INTERPRETER_NOT_DECLARED"


def test_native_binary_dynamic_elf_with_a_null_interpreter_is_refused(
    tmp_path: Path,
) -> None:
    """A dynamically loaded ELF names a loader the descriptor never froze."""
    root = tmp_path / "stage"
    binary = _elf(
        root / "artifacts" / "opencode" / "1.18.5" / "bin" / "opencode",
        interpreter="/lib64/ld-linux-x86-64.so.2",
    )
    harden(binary.parent)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve_native(tmp_path, _undeclared(native_binary_descriptor(binary)))
    assert excinfo.value.rule == "INTERPRETER_NOT_DECLARED"


def test_system_interpreter_outside_a_trusted_chain_is_refused(
    tmp_path: Path,
) -> None:
    """Honestly declaring ``/bin/sh`` is not enough to make it trustworthy.

    Declaring the real interpreter is necessary, not sufficient: the host's
    ``/bin`` is a symlink, so a Binding naming it fails the same ancestor rule
    every other artifact obeys. Preparing an immutable interpreter path is an
    operator installation action, exactly like preparing the artifact root.
    """
    root = tmp_path / "stage"
    binary = make_native_binary(root, body="#!/bin/sh\nexit 0\n")
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve_native(tmp_path, native_binary_descriptor(binary))
    assert excinfo.value.rule == "SYMLINKED_ANCESTOR"


def test_native_binary_interpreter_mismatch_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    interpreter = stage_interpreter(root)
    binary = make_native_binary(root, body=f"#!{interpreter}\nexit 0\n")
    descriptor = native_binary_descriptor(binary)
    other = stage_interpreter(root, name="other-sh")
    descriptor["interpreter"] = str(other)
    descriptor["interpreter_sha256"] = sha256_file(other)
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve_native(tmp_path, descriptor)
    assert excinfo.value.rule == "INTERPRETER_MISMATCH"


def test_native_binary_env_style_shebang_is_refused(tmp_path: Path) -> None:
    """``#!/usr/bin/env x`` resolves through PATH: an unfrozen runtime."""
    root = tmp_path / "stage"
    binary = make_native_binary(root, body="#!/usr/bin/env node\n")
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve_native(tmp_path, native_binary_descriptor(binary))
    assert excinfo.value.rule == "INTERPRETER_INDIRECT"


def test_native_binary_static_elf_needs_no_interpreter(tmp_path: Path) -> None:
    """Companion to the refusals: a static image legitimately declares none."""
    root = tmp_path / "stage"
    binary = _elf(
        root / "artifacts" / "opencode" / "1.18.5" / "bin" / "opencode",
        interpreter=None,
    )
    harden(binary.parent)
    resolved = _resolve_native(tmp_path, native_binary_descriptor(binary))
    assert resolved.slot("agent_cli").descriptor["interpreter"] is None


def test_native_binary_declaring_its_real_interpreter_is_accepted(
    tmp_path: Path,
) -> None:
    root = tmp_path / "stage"
    interpreter = stage_interpreter(root)
    binary = make_native_binary(root, body=f"#!{interpreter}\nexit 0\n")
    resolved = _resolve_native(tmp_path, native_binary_descriptor(binary))
    assert resolved.slot("agent_cli").descriptor["interpreter"] == str(interpreter)


# ---------------------------------------------------------------------------
# C1 — the probe's output bound is a memory bound, not a post-hoc slice
# ---------------------------------------------------------------------------


def test_probe_output_is_bounded_in_memory(tmp_path: Path) -> None:
    """A firehose CLI must not be buffered whole before truncation."""
    import tracemalloc

    script = tmp_path / "flood-cli"
    script.write_text(
        "#!/bin/sh\nprintf '%s\\n' '1.18.5'\n"
        "dd if=/dev/zero bs=1048576 count=64 2>/dev/null\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    rule = OPENCODE_NATIVE_ACP.contract.version_probe

    # Self-check: prove the fixture really floods, so this test can never pass
    # vacuously on a host where the flood command is missing.
    import subprocess

    produced = 0
    with subprocess.Popen([str(script)], stdout=subprocess.PIPE) as child:
        assert child.stdout is not None
        while True:
            block = child.stdout.read(1 << 20)
            if not block:
                break
            produced += len(block)
    assert produced > 32 * 1024 * 1024, f"fixture produced only {produced} bytes"

    tracemalloc.start()
    try:
        observed = rb.probe_cli_version(executable=str(script), rule=rule)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert observed == "1.18.5"
    # The rule bounds output at 8 KiB; a whole-output buffer would be 64 MiB.
    assert peak < 8 * 1024 * 1024, f"probe buffered {peak} bytes"


def test_probe_drains_both_pipes_without_deadlocking(tmp_path: Path) -> None:
    """A child that floods stderr must not wedge the probe on a full pipe."""
    import dataclasses

    script = tmp_path / "stderr-flood-cli"
    script.write_text(
        "#!/bin/sh\nprintf '%s\\n' '1.18.5'\n"
        "dd if=/dev/zero bs=1048576 count=8 2>/dev/null 1>&2\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    rule = dataclasses.replace(
        OPENCODE_NATIVE_ACP.contract.version_probe, timeout_seconds=20.0
    )
    assert rb.probe_cli_version(executable=str(script), rule=rule) == "1.18.5"


def test_probe_timeout_kills_and_reaps_the_child(tmp_path: Path) -> None:
    """The bound is enforced by killing the child, not by leaking it."""
    import dataclasses
    import subprocess

    marker = tmp_path / "still-running"
    script = tmp_path / "hanging-cli"
    script.write_text(
        "#!/bin/sh\nprintf '%s\\n' '1.18.5'\n"
        f"touch '{marker}'\nsleep 60\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    rule = dataclasses.replace(
        OPENCODE_NATIVE_ACP.contract.version_probe, timeout_seconds=0.5
    )

    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.probe_cli_version(executable=str(script), rule=rule)
    assert excinfo.value.rule == "PROBE_TIMEOUT"
    assert marker.exists()  # the child really started
    # Reaped, not leaked: no probe child survives the refusal.
    import shutil

    if shutil.which("pgrep") is None:  # pragma: no cover - host-dependent
        pytest.skip("pgrep is unavailable; cannot assert the child was reaped")
    survivors = subprocess.run(
        ["pgrep", "-f", str(script)], capture_output=True, text=True
    )
    assert survivors.stdout.strip() == ""


def _proc_stat_after_comm(pid: int) -> list[str] | None:
    """``/proc/<pid>/stat`` fields from ``state`` on, or ``None`` when gone.

    Splitting after the final ``)`` keeps a ``comm`` that contains spaces or
    parentheses from shifting every later field.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    fields = raw.rpartition(")")[2].split()
    return fields if len(fields) > 19 else None


def _running_since(pid: int) -> str | None:
    """The pid's start time, or ``None`` when it is gone or already a zombie.

    ``starttime`` (field 22) is fixed for the life of a pid, so it identifies
    *this* process even if the number is later recycled onto another one.
    """
    fields = _proc_stat_after_comm(pid)
    if fields is None or fields[0] == "Z":
        return None
    return fields[19]


def _still_running(pid: int, started_at: str | None) -> bool:
    """True while the exact process first observed as ``started_at`` is live."""
    if started_at is None:
        return False
    return _running_since(pid) == started_at


def _recorded_pid(pidfile: Path, *, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    while True:
        try:
            text = pidfile.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text.isdigit():
            return int(text)
        assert time.monotonic() < deadline, f"no descendant pid was recorded in {pidfile}"
        time.sleep(0.02)


def test_probe_timeout_kills_the_whole_probe_tree(tmp_path: Path) -> None:
    """A descendant that outlives the direct child must not survive the bound.

    Here the probe's shell records a background pid and exits at once, so the
    direct child is already gone when the deadline expires while the descendant
    keeps running — still holding the probe's stdout and stderr. Killing only
    the direct child would bound nothing.
    """
    import dataclasses

    if _running_since(os.getpid()) is None:  # pragma: no cover - host-dependent
        pytest.skip("/proc is unavailable; cannot observe descendant liveness")

    pidfile = tmp_path / "descendant.pid"
    script = tmp_path / "orphaning-cli"
    script.write_text(
        "#!/bin/sh\nprintf '%s\\n' '1.18.5'\n"
        f"sleep 60 &\nprintf '%s\\n' \"$!\" > '{pidfile}'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    rule = dataclasses.replace(
        OPENCODE_NATIVE_ACP.contract.version_probe, timeout_seconds=0.5
    )

    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.probe_cli_version(executable=str(script), rule=rule)
    assert excinfo.value.rule == "PROBE_TIMEOUT"
    assert "sleep" not in str(excinfo.value)  # sanitized: no probe output leaks

    descendant = _recorded_pid(pidfile)
    started_at = _running_since(descendant)
    try:
        deadline = time.monotonic() + 5.0
        while _still_running(descendant, started_at):
            assert time.monotonic() < deadline, (
                f"probe descendant {descendant} survived the PROBE_TIMEOUT refusal"
            )
            time.sleep(0.05)
    finally:
        # Never leak the descendant onto the host, however this test ends.
        if _still_running(descendant, started_at):
            os.kill(descendant, signal.SIGKILL)


def _assert_fixture_leaks_a_descendant(script: Path, pidfile: Path) -> None:
    """Prove the fixture really outlives its own direct child, then clean up.

    Run outside the probe, so the assertion is about the fixture alone. Without
    this the containment test below could pass vacuously on a host where the
    fork or the redirection does not behave as written.
    """
    import subprocess

    with subprocess.Popen(  # noqa: S603 - test fixture, fixed argv
        [str(script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    ) as child:
        assert child.wait() == 0
    stray = _recorded_pid(pidfile)
    started_at = _running_since(stray)
    try:
        assert started_at is not None, (
            f"fixture descendant {stray} did not outlive the direct child"
        )
    finally:
        if _still_running(stray, started_at):
            os.kill(stray, signal.SIGKILL)
    # A stale pid must not be mistaken for the probe's own descendant.
    pidfile.unlink()


def test_probe_success_kills_the_whole_probe_tree(tmp_path: Path) -> None:
    """Containment is not a timeout remedy: a *successful* probe leaks nothing.

    The probe prints a valid version, forks a long-lived descendant whose
    output is redirected away from the probe's pipes, records that descendant's
    exact pid and exits 0. Both pipes therefore reach EOF with the direct child
    already reapable, so nothing forces the deadline: teardown that only runs
    when the child was *not* reaped would leave the descendant running.
    """
    if _running_since(os.getpid()) is None:  # pragma: no cover - host-dependent
        pytest.skip("/proc is unavailable; cannot observe descendant liveness")

    pidfile = tmp_path / "descendant.pid"
    script = tmp_path / "forking-cli"
    script.write_text(
        "#!/bin/sh\nprintf '%s\\n' '1.18.5'\n"
        # Redirected away from the probe's pipes on purpose: both reach EOF as
        # soon as the direct child exits. The pid is recorded before that exit,
        # so observing EOF means the pidfile is already written.
        f"sleep 60 >/dev/null 2>&1 &\nprintf '%s\\n' \"$!\" > '{pidfile}'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    _assert_fixture_leaks_a_descendant(script, pidfile)

    observed = rb.probe_cli_version(
        executable=str(script), rule=OPENCODE_NATIVE_ACP.contract.version_probe
    )
    # Success is also the proof that the group kill left the direct child's real
    # exit status alone: a SIGKILL landing on it would surface as PROBE_FAILED.
    assert observed == "1.18.5"

    descendant = _recorded_pid(pidfile)
    started_at = _running_since(descendant)
    try:
        deadline = time.monotonic() + 5.0
        while _still_running(descendant, started_at):
            assert time.monotonic() < deadline, (
                f"probe descendant {descendant} survived a successful probe"
            )
            time.sleep(0.05)
    finally:
        # Never leak the descendant onto the host, however this test ends.
        if _still_running(descendant, started_at):
            os.kill(descendant, signal.SIGKILL)


def test_probe_nonzero_exit_kills_the_whole_probe_tree(tmp_path: Path) -> None:
    """The same containment invariant on the non-zero completion path."""
    if _running_since(os.getpid()) is None:  # pragma: no cover - host-dependent
        pytest.skip("/proc is unavailable; cannot observe descendant liveness")

    pidfile = tmp_path / "descendant.pid"
    script = tmp_path / "failing-forking-cli"
    script.write_text(
        "#!/bin/sh\nprintf '%s\\n' '1.18.5'\n"
        f"sleep 60 >/dev/null 2>&1 &\nprintf '%s\\n' \"$!\" > '{pidfile}'\nexit 7\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.probe_cli_version(
            executable=str(script), rule=OPENCODE_NATIVE_ACP.contract.version_probe
        )
    assert excinfo.value.rule == "PROBE_FAILED"
    assert "sleep" not in str(excinfo.value)  # sanitized: no probe output leaks

    descendant = _recorded_pid(pidfile)
    started_at = _running_since(descendant)
    try:
        deadline = time.monotonic() + 5.0
        while _still_running(descendant, started_at):
            assert time.monotonic() < deadline, (
                f"probe descendant {descendant} survived the PROBE_FAILED refusal"
            )
            time.sleep(0.05)
    finally:
        if _still_running(descendant, started_at):
            os.kill(descendant, signal.SIGKILL)


# ---------------------------------------------------------------------------
# B6 — the probe's exit status and its process tree under an external reaper
# ---------------------------------------------------------------------------

# Stage 1 sets SIGCHLD to SIG_IGN and re-execs, so stage 2 never installed the
# disposition itself. That is the shape a service manager or a parent reaper
# leaves behind, and — unlike a disposition Python set — the only shape that has
# to be read back from the kernel rather than from ``signal.getsignal``.
_SIGCHLD_IGNORED_PROBE_DRIVER = '''\
import json
import os
import signal
import sys

if os.environ.get("ARS_PROBE_STAGE") != "2":
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    os.environ["ARS_PROBE_STAGE"] = "2"
    os.execv(sys.executable, [sys.executable, __file__, *sys.argv[1:]])

from agent_run_supervisor.native_acp import runtime_binding as rb
from agent_run_supervisor.native_acp.profile import OPENCODE_NATIVE_ACP

report = {"stage": os.environ.get("ARS_PROBE_STAGE")}
try:
    report["version"] = rb.probe_cli_version(
        executable=sys.argv[1], rule=OPENCODE_NATIVE_ACP.contract.version_probe
    )
except rb.BindingRefusal as refusal:
    report["rule"] = refusal.rule
    report["message"] = refusal.message
print(json.dumps(report))
'''


def test_inherited_ignored_sigchld_probe_never_succeeds_and_leaks_nothing(
    tmp_path: Path,
) -> None:
    """B6: an inherited ``SIGCHLD=SIG_IGN`` must not yield a leaked descendant.

    Under an ignored SIGCHLD the kernel reaps the direct child the instant it
    exits. Two things are destroyed at once: the real exit status, which CPython
    then reports as a fabricated ``0``, and the pid reservation — and that pid
    *is* the probe's process-group id, so the group holding the surviving
    descendant can no longer be signalled with any proof the number is still the
    probe's. A probe that cannot observe its own child's exit cannot prove
    containment, so it must not report a version.

    Run in a separate process on purpose: the disposition is the thing under
    test, and this suite's own process must not acquire it.
    """
    import subprocess
    import sys

    if _running_since(os.getpid()) is None:  # pragma: no cover - host-dependent
        pytest.skip("/proc is unavailable; cannot observe descendant liveness")

    pidfile = tmp_path / "descendant.pid"
    script = tmp_path / "forking-cli"
    script.write_text(
        "#!/bin/sh\nprintf '%s\\n' '1.18.5'\n"
        f"sleep 60 >/dev/null 2>&1 &\nprintf '%s\\n' \"$!\" > '{pidfile}'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    driver = tmp_path / "sigchld_ignored_probe.py"
    driver.write_text(_SIGCHLD_IGNORED_PROBE_DRIVER, encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(entry for entry in sys.path if entry)
    env.pop("ARS_PROBE_STAGE", None)
    completed = subprocess.run(  # noqa: S603 - test driver, fixed argv
        [sys.executable, str(driver), str(script)],
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )

    # Read the descendant's exact identity before asserting anything, so the
    # cleanup below can reach it however this test ends — including the RED run,
    # where the probe really does return a version and leave the sleep behind.
    descendant: int | None = None
    started_at: str | None = None
    if pidfile.exists():
        descendant = _recorded_pid(pidfile)
        started_at = _running_since(descendant)
    try:
        assert completed.returncode == 0, completed.stderr
        report = json.loads(completed.stdout.strip().splitlines()[-1])
        assert report["stage"] == "2"  # inherited across exec, not set in-process
        assert "version" not in report, report
        assert report["rule"] in ("PROBE_REAPER_UNSAFE", "PROBE_STATUS_LOST"), report
        assert "sleep" not in report["message"]  # sanitized: no probe output leaks
        if descendant is not None:
            deadline = time.monotonic() + 5.0
            while _still_running(descendant, started_at):
                assert time.monotonic() < deadline, (
                    f"probe descendant {descendant} survived an ignored-SIGCHLD probe"
                )
                time.sleep(0.05)
    finally:
        if descendant is not None and _still_running(descendant, started_at):
            os.kill(descendant, signal.SIGKILL)


def test_probe_refuses_before_launching_when_children_cannot_be_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate runs before the fork: an uncontainable probe is never started.

    Repairing the disposition here instead would mean mutating a signal setting
    shared with every other child of the calling process, so the only outcome
    that cannot leak is to refuse without launching.
    """
    marker = tmp_path / "started"
    script = tmp_path / "never-launched-cli"
    script.write_text(
        f"#!/bin/sh\ntouch '{marker}'\nprintf '%s\\n' '1.18.5'\n", encoding="utf-8"
    )
    script.chmod(0o755)
    monkeypatch.setattr(rb, "_sigchld_ignored", lambda: True)

    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.probe_cli_version(
            executable=str(script), rule=OPENCODE_NATIVE_ACP.contract.version_probe
        )
    assert excinfo.value.rule == "PROBE_REAPER_UNSAFE"
    assert not marker.exists()  # nothing launched, so nothing can be leaked


def test_sigchld_is_not_ignored_under_the_test_runner() -> None:
    """Guards the gate against silently becoming a blanket refusal.

    Every probe test in this module would pass vacuously if the runner's own
    disposition tripped the gate, so the detector's answer here is asserted
    rather than assumed.
    """
    assert rb._sigchld_ignored() is False


# ---------------------------------------------------------------------------
# B6 lifecycle — the probe's process group is *owned*, never merely observed
# ---------------------------------------------------------------------------
#
# The rule the tests below encode: a process-group signal aimed by *number* can
# never be made safe on a kernel before 6.9, because no observation can be made
# atomic with the signal it authorises. ``killpg(pgid, 0)`` answers about the
# instant it ran and nothing later; a ``pidfd`` names a process rather than a
# group, and does not reserve the number either — the kernel releases a pid when
# the last task is detached from it, not when the last reference to it goes.
#
# So the window is removed rather than narrowed. Every process-group signal in
# the probe is issued *from inside the group* with target ``0``, which the kernel
# resolves from the calling task; that task is alive because it is executing the
# call, so there is no number in play and no instant at which the target could
# have become an unrelated group. The supervisor aims none at all.


def _facts_cli(
    tmp_path: Path,
    *,
    name: str,
    facts: Path,
    descendant: Path | None = None,
    exit_code: int = 0,
    version: str = "1.18.5",
) -> Path:
    """A probe CLI that records, from inside the probe, whose child it is.

    Its parent's ``/proc/<pid>/stat`` is read while the CLI is still running, so
    what lands in ``facts`` is the parent's state *during* the probe rather than
    a claim made about it afterwards, when the process may be gone. Only shell
    builtins are used, so the fixture needs nothing on the hermetic ``PATH``.
    """
    lines = [
        "#!/bin/sh",
        f"printf '%s\\n' '{version}'",
        "read _pid _comm _state _ppid _rest < /proc/$$/stat",
        "read _apid _acomm _astate _appid _apgrp _asid _arest < /proc/$_ppid/stat",
        'printf \'%s %s %s %s %s %s\\n\' "$$" "$_apid" "$_astate" '
        f"\"$_appid\" \"$_apgrp\" \"$_asid\" > '{facts}'",
    ]
    if descendant is not None:
        # Redirected away from the probe's pipes on purpose: both reach EOF as
        # soon as the CLI exits, so nothing forces the deadline and teardown has
        # to be unconditional to reach this survivor.
        lines.append("sleep 60 >/dev/null 2>&1 &")
        lines.append(f"printf '%s\\n' \"$!\" > '{descendant}'")
    lines.append(f"exit {exit_code}")
    script = tmp_path / name
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _recorded_facts(path: Path, *, timeout: float = 5.0) -> dict[str, Any]:
    """The six fields :func:`_facts_cli` records, once they are all there."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            fields = path.read_text(encoding="utf-8").split()
        except OSError:
            fields = []
        if len(fields) == 6:
            return {
                "cli": int(fields[0]),
                "parent": int(fields[1]),
                "parent_state": fields[2],
                "parent_ppid": int(fields[3]),
                "parent_pgrp": int(fields[4]),
                "parent_session": int(fields[5]),
            }
        assert time.monotonic() < deadline, f"the probe recorded no facts in {path}"
        time.sleep(0.02)


def _assert_descendant_dies(pidfile: Path, *, why: str) -> None:
    """The recorded descendant must not outlive the probe, however it ended."""
    descendant = _recorded_pid(pidfile)
    started_at = _running_since(descendant)
    try:
        deadline = time.monotonic() + 5.0
        while _still_running(descendant, started_at):
            assert time.monotonic() < deadline, (
                f"probe descendant {descendant} survived {why}"
            )
            time.sleep(0.05)
    finally:
        # Never leak the descendant onto the host, however this test ends.
        if _still_running(descendant, started_at):
            os.kill(descendant, signal.SIGKILL)


class _GroupSignalWitness:
    """Every process-group signal this process aims by number.

    ``killpg(pgid, 0)`` is answered as *success* on purpose: an implementation
    that treats a liveness check as a licence is handed the most favourable
    possible answer to it and is still caught, because what gets recorded is the
    signal itself rather than the reasoning behind it. ``kill`` with a
    non-positive pid is the same call spelled differently and is recorded too.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[int, int]] = []
        real_killpg, real_kill = os.killpg, os.kill

        def killpg(pgid: int, sig: int) -> None:
            self.calls.append((pgid, sig))
            if sig == 0:
                return  # "the old group is still non-empty" — and then it empties
            real_killpg(pgid, sig)

        def kill(pid: int, sig: int) -> None:
            if pid <= 0:  # a group signal wearing ``kill``'s clothes
                self.calls.append((pid, sig))
            real_kill(pid, sig)

        monkeypatch.setattr(os, "killpg", killpg)
        monkeypatch.setattr(os, "kill", kill)


def test_probe_never_aims_a_process_group_signal_by_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B6: no liveness check may authorise a group signal, because none can.

    This is the sequence that has to become impossible: an external reap makes
    ``waitid`` answer ``ECHILD``, ``killpg(pgid, 0)`` still finds the old group
    non-empty, the group then empties and the number is handed to an unrelated
    one, and ``killpg(pgid, SIGKILL)`` goes out anyway. A fresh check immediately
    before the signal does not close that window — it *is* the window.

    ``waitid`` is made to answer ``ECHILD`` for the whole run, which is exactly
    what an external reap looks like from the inside, and the liveness check is
    answered as success. What is asserted is not that the sequence did not happen
    but the property that makes it unreachable: this process aims no
    process-group signal at any number at all.
    """
    if _running_since(os.getpid()) is None:  # pragma: no cover - host-dependent
        pytest.skip("/proc is unavailable; cannot observe descendant liveness")

    facts = tmp_path / "facts"
    pidfile = tmp_path / "descendant.pid"
    script = _facts_cli(
        tmp_path,
        name="reaped-forking-cli",
        facts=facts,
        descendant=pidfile,
        exit_code=7,
    )

    def externally_reaped(*_args: Any, **_kwargs: Any) -> Any:
        raise ChildProcessError("modelled external reap")

    monkeypatch.setattr(os, "waitid", externally_reaped)
    witness = _GroupSignalWitness(monkeypatch)

    caller_group = os.getpgrp()
    outcome: dict[str, Any] = {}
    started = time.monotonic()
    try:
        outcome["version"] = rb.probe_cli_version(
            executable=str(script), rule=OPENCODE_NATIVE_ACP.contract.version_probe
        )
    except rb.BindingRefusal as refusal:
        outcome["rule"] = refusal.rule
        outcome["message"] = refusal.message
    elapsed = time.monotonic() - started

    assert "version" not in outcome, outcome
    assert witness.calls == [], (
        "the probe aimed a process-group signal by number, so a reap between the "
        f"check and the signal can still misdirect it: CALLS={witness.calls}"
    )
    assert os.getpgrp() == caller_group  # never the caller's own group
    assert elapsed < 5.0  # bounded, not waited out
    _assert_descendant_dies(pidfile, why="a modelled external reap")


def test_probe_cli_runs_under_a_live_code_owned_group_leader(
    tmp_path: Path,
) -> None:
    """B6: the group id belongs to a live process this code owns, not to the CLI.

    Recorded by the CLI itself, mid-probe, out of ``/proc``: its parent is not
    this process, it is not a zombie, and it leads both the process group and the
    session whose id is its own pid. A live task cannot be reaped — reaping is
    what a *zombie* is for — so no reaper anywhere can release that number while
    those facts hold, and the group can never be empty while its leader is in it.

    That is the whole lifetime argument, and it is a fact about a process rather
    than an observation carried forward from an earlier instant.
    """
    if _running_since(os.getpid()) is None:  # pragma: no cover - host-dependent
        pytest.skip("/proc is unavailable; cannot observe descendant liveness")

    facts = tmp_path / "facts"
    pidfile = tmp_path / "descendant.pid"
    script = _facts_cli(
        tmp_path, name="anchored-cli", facts=facts, descendant=pidfile, exit_code=7
    )

    with pytest.raises(rb.BindingRefusal) as excinfo:
        rb.probe_cli_version(
            executable=str(script), rule=OPENCODE_NATIVE_ACP.contract.version_probe
        )
    assert excinfo.value.rule == "PROBE_FAILED"  # the real 7, not a version

    observed = _recorded_facts(facts)
    anchor = observed["parent"]
    assert anchor != os.getpid(), (
        "the probed CLI is this process's own child, so any reaper here can take "
        "its status and release the number its group is named after"
    )
    assert anchor > 1
    assert observed["parent_state"] != "Z", (
        "the group id was held by a zombie, which a reaper can release at will"
    )
    assert observed["parent_pgrp"] == anchor, "the CLI's parent does not lead its group"
    assert observed["parent_session"] == anchor, "the group is not its own session"
    assert observed["parent_ppid"] == os.getpid(), "the group leader is not code-owned"
    assert anchor != os.getpgrp()  # never the caller's own group
    _assert_descendant_dies(pidfile, why="a probe under a live group leader")


def test_probe_exit_status_is_out_of_reach_of_any_reaper_in_this_process(
    tmp_path: Path,
) -> None:
    """B6: a genuine ``7`` stays a ``7`` while a reaper drains this process.

    The reaper is the classic ``waitpid(-1, WNOHANG)`` drain, running on another
    thread for the whole probe and free to take anything this process owns. It
    cannot take the CLI's status, and not because it lost a race: the CLI is not
    this process's child at all, so ``wait`` in this process can never name it.
    The status is collected by the code-owned process that *is* its parent and
    arrives over a pipe, where no reaper can reach it.
    """
    import threading

    if _running_since(os.getpid()) is None:  # pragma: no cover - host-dependent
        pytest.skip("/proc is unavailable; cannot observe descendant liveness")

    facts = tmp_path / "facts"
    pidfile = tmp_path / "descendant.pid"
    script = _facts_cli(
        tmp_path, name="drained-cli", facts=facts, descendant=pidfile, exit_code=7
    )

    reaped: list[int] = []
    stop = threading.Event()

    def drain() -> None:
        while not stop.is_set():
            try:
                pid, _status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                pid = 0
            except OSError:  # pragma: no cover - host-dependent
                return
            if pid > 0:
                reaped.append(pid)
            else:
                time.sleep(0.001)

    reaper = threading.Thread(target=drain, name="adversarial-reaper", daemon=True)
    reaper.start()
    try:
        with pytest.raises(rb.BindingRefusal) as excinfo:
            rb.probe_cli_version(
                executable=str(script), rule=OPENCODE_NATIVE_ACP.contract.version_probe
            )
    finally:
        stop.set()
        reaper.join(timeout=5.0)

    assert excinfo.value.rule == "PROBE_FAILED"  # never a version
    assert "sleep" not in str(excinfo.value)  # sanitized: no probe output leaks
    assert not _is_sha256_text(str(excinfo.value))

    observed = _recorded_facts(facts)
    assert observed["parent"] != os.getpid(), (
        "the CLI is this process's own child, so the drain above was competing "
        "for its exit status rather than being structurally unable to see it"
    )
    assert observed["cli"] not in reaped, (
        f"a reaper in this process consumed the probed CLI: {reaped}"
    )
    _assert_descendant_dies(pidfile, why="an adversarial reaper drain")


# A *caught* SIGCHLD is the disposition the pre-fork gate deliberately lets
# through, because a handler cannot be told apart from a reaping one. This is
# the reaping kind: the classic ``waitpid(-1, WNOHANG)`` drain loop, which will
# happily consume a child it never started.
_SIGCHLD_REAPING_HANDLER_PROBE_DRIVER = '''\
import json
import os
import signal
import sys

reaped = []


def drain(signum, frame):
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return
        reaped.append(pid)


signal.signal(signal.SIGCHLD, drain)

from agent_run_supervisor.native_acp import runtime_binding as rb
from agent_run_supervisor.native_acp.profile import OPENCODE_NATIVE_ACP

report = {"handler": signal.getsignal(signal.SIGCHLD) is drain}
try:
    report["version"] = rb.probe_cli_version(
        executable=sys.argv[1], rule=OPENCODE_NATIVE_ACP.contract.version_probe
    )
except rb.BindingRefusal as refusal:
    report["rule"] = refusal.rule
    report["message"] = refusal.message
report["reaped"] = reaped
print(json.dumps(report))
'''


def test_caught_reaping_sigchld_probe_never_succeeds_and_leaks_nothing(
    tmp_path: Path,
) -> None:
    """B6: a caught reaping handler has nothing left to compete for.

    ``_require_reapable_children`` refuses ``SIG_IGN`` before the fork but lets a
    *caught* ``SIGCHLD`` through, because a handler that reaps is indistinguishable
    from one that does not. It used to be a live race — the handler could take
    the probed child's status either before its exit was observed or after, and
    which side it landed on was a scheduling detail. It is no longer a race at
    all: the probed CLI is not this process's child, so ``waitpid(-1)`` here can
    never name it, and the ``7`` it really exited with is asserted exactly.

    Run in a separate process on purpose: the disposition is the thing under
    test, and this suite's own process must not acquire it.
    """
    import subprocess
    import sys

    if _running_since(os.getpid()) is None:  # pragma: no cover - host-dependent
        pytest.skip("/proc is unavailable; cannot observe descendant liveness")

    pidfile = tmp_path / "descendant.pid"
    script = tmp_path / "handler-reaped-forking-cli"
    script.write_text(
        "#!/bin/sh\nprintf '%s\\n' '1.18.5'\n"
        f"sleep 60 >/dev/null 2>&1 &\nprintf '%s\\n' \"$!\" > '{pidfile}'\nexit 7\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    driver = tmp_path / "sigchld_handler_probe.py"
    driver.write_text(_SIGCHLD_REAPING_HANDLER_PROBE_DRIVER, encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(entry for entry in sys.path if entry)
    completed = subprocess.run(  # noqa: S603 - test driver, fixed argv
        [sys.executable, str(driver), str(script)],
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )

    descendant: int | None = None
    started_at: str | None = None
    if pidfile.exists():
        descendant = _recorded_pid(pidfile)
        started_at = _running_since(descendant)
    try:
        assert completed.returncode == 0, completed.stderr
        report = json.loads(completed.stdout.strip().splitlines()[-1])
        assert report["handler"] is True, report  # the handler really was armed
        assert "version" not in report, report
        assert report["rule"] == "PROBE_FAILED", report
        assert "sleep" not in report["message"]  # sanitized: no probe output leaks
        if descendant is not None:
            deadline = time.monotonic() + 5.0
            while _still_running(descendant, started_at):
                assert time.monotonic() < deadline, (
                    f"probe descendant {descendant} survived a caught-SIGCHLD probe"
                )
                time.sleep(0.05)
    finally:
        if descendant is not None and _still_running(descendant, started_at):
            os.kill(descendant, signal.SIGKILL)


def _open_fds() -> set[int]:
    """Every descriptor this process currently holds.

    Probed with ``F_GETFD`` rather than by listing ``/proc/self/fd``, because
    listing a directory opens one itself and would put a number in the answer
    that is not in the answer a moment later.
    """
    import fcntl

    live = set()
    for fd in range(0, 1024):
        try:
            fcntl.fcntl(fd, fcntl.F_GETFD)
        except OSError:
            continue
        live.add(fd)
    return live


def test_probe_anchor_owns_its_group_until_its_own_group_signal_kills_it(
    tmp_path: Path,
) -> None:
    """B6: the lifetime anchor, end to end, through the seam that implements it.

    While the probe is in flight the anchor is a live, unreaped child of this
    process leading a group and session whose id is its own pid. A reaper drain
    here takes nothing from it — not by losing a race, but because reaping is
    something only a *zombie* can undergo, and the anchor is alive.

    Teardown is a message, not a signal this process aims: closing the control
    pipe is the anchor's only exit, and on the way out it signals *its own*
    group, named by no number. The kernel's own receipt for that is the anchor's
    exit status. A process killed by the group signal it issued was, necessarily,
    alive and in that group at the instant of the call — which is the one thing a
    ``killpg`` aimed by number from outside can never establish about itself.
    """
    if _running_since(os.getpid()) is None:  # pragma: no cover - host-dependent
        pytest.skip("/proc is unavailable; cannot observe anchor liveness")

    facts = tmp_path / "facts"
    pidfile = tmp_path / "descendant.pid"
    script = _facts_cli(
        tmp_path, name="seam-cli", facts=facts, descendant=pidfile, exit_code=7
    )
    rule = OPENCODE_NATIVE_ACP.contract.version_probe

    before = _open_fds()
    anchor = rb._ProbeAnchor.launch([str(script)], cwd=str(tmp_path))
    try:
        stat = _proc_stat_after_comm(anchor.pid)
        assert stat is not None and stat[0] != "Z", stat  # live, so unreapable
        assert int(stat[2]) == anchor.pid, "the anchor does not lead its own group"
        assert int(stat[3]) == anchor.pid, "the anchor does not lead its own session"
        assert int(stat[1]) == os.getpid(), "the anchor is not this code's own child"
        assert anchor.pid not in (0, 1, os.getpgrp())

        # An external reaper in this very process, while the anchor is alive.
        try:
            taken = os.waitpid(-1, os.WNOHANG)[0]
        except ChildProcessError:
            taken = 0
        assert taken != anchor.pid, "a live anchor was reaped, which cannot happen"

        stdout, _stderr, returncode = anchor.capture(
            limit=rule.max_output_bytes, timeout=rule.timeout_seconds
        )
        assert b"1.18.5" in stdout
        assert returncode == 7, "the CLI's real status did not survive its collection"

        observed = _recorded_facts(facts)
        assert observed["parent"] == anchor.pid  # the CLI is the anchor's child

        # The adversary strikes *after* the status was observed and before
        # teardown — the window that used to turn a 7 into a version.
        try:
            os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            pass
        still = _proc_stat_after_comm(anchor.pid)
        assert still is not None and still[0] != "Z", still
    finally:
        anchor.close()

    # The anchor reported the group it was about to signal, and then died of it.
    assert anchor.teardown_report == (anchor.pid, anchor.pid), anchor.teardown_report
    assert anchor.returncode == -signal.SIGKILL, anchor.returncode
    assert _open_fds() == before, "the anchor did not give every descriptor back"
    _assert_descendant_dies(pidfile, why="an anchored probe teardown")


def _anchor_ignored_signals() -> list[int]:
    """The signals the anchor must ignore and the CLI must *not* inherit ignored."""
    numbers = []
    for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
        number = getattr(signal, name, None)
        if number is not None:
            numbers.append(number)
    return numbers


def _sig_ignored(mask: str, number: int) -> bool:
    """Read one signal out of a ``/proc/<pid>/status`` ``SigIgn:`` bitmask."""
    return bool(int(mask, 16) >> (number - 1) & 1)


def _disposition_cli(tmp_path: Path, *, name: str, masks: Path) -> Path:
    """A probe CLI that records which signals it and its anchor ignore.

    Both masks are read out of ``/proc`` from inside the probe while both
    processes are running, so what lands in ``masks`` is the kernel's own view
    of the split rather than a claim made about it from outside. Only shell
    builtins are used, so the fixture needs nothing on the hermetic ``PATH``.
    """
    script = tmp_path / name
    script.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '1.18.5'\n"
        "read -r _pid _comm _state _ppid _rest < /proc/$$/stat\n"
        "mine=\ntheirs=\n"
        "while read -r key value; do\n"
        '  case "$key" in SigIgn:) mine="$value";; esac\n'
        "done < /proc/$$/status\n"
        "while read -r key value; do\n"
        '  case "$key" in SigIgn:) theirs="$value";; esac\n'
        "done < /proc/$_ppid/status\n"
        f"printf '%s %s %s\\n' \"$_ppid\" \"$mine\" \"$theirs\" > '{masks}'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_anchor_ignores_sigpipe_while_the_probed_cli_keeps_the_default(
    tmp_path: Path,
) -> None:
    """B6: the disposition split teardown depends on, pinned from inside the probe.

    Teardown names the group it is about to signal *before* it signals it, and it
    writes that into a pipe a dead supervisor has already closed — so the report
    is expected to fail on the one path where the group most needs tearing down.
    The anchor must therefore ignore ``SIGPIPE``: ignored it is an ``EPIPE`` on
    the way to ``killpg(0, ...)``, defaulted it is a fatal signal instead of one.

    The probed CLI must not inherit any of that. It starts from the dispositions
    ``subprocess(restore_signals=True)`` would have given it, handed over by
    ``setsigdef`` — which is a statement about the child, and the only reason the
    anchor can hold a disposition of its own without changing what the CLI sees.

    ``SIGCHLD`` is read off the same evidence because it points the other way:
    the anchor must have it *defaulted*, or the CLI's real status is auto-reaped
    out of reach of the process that exists to report it.
    """
    if _running_since(os.getpid()) is None:  # pragma: no cover - host-dependent
        pytest.skip("/proc is unavailable; cannot observe signal dispositions")

    masks = tmp_path / "sigign"
    script = _disposition_cli(tmp_path, name="disposition-cli", masks=masks)

    observed = rb.probe_cli_version(
        executable=str(script), rule=OPENCODE_NATIVE_ACP.contract.version_probe
    )
    assert observed == "1.18.5"  # the split did not cost a working probe

    deadline = time.monotonic() + 5.0
    while True:
        fields = masks.read_text(encoding="ascii").split() if masks.exists() else []
        if len(fields) == 3:
            break
        assert time.monotonic() < deadline, f"the probe recorded no masks in {masks}"
        time.sleep(0.02)
    anchor, cli_mask, anchor_mask = fields
    assert int(anchor) > 1, (
        "the CLI was already an orphan, so the second mask is somebody else's"
    )
    for number in _anchor_ignored_signals():
        assert _sig_ignored(anchor_mask, number), (
            f"the anchor left signal {number} fatal to itself, so its own teardown "
            f"report can end it before it ever signals its group: {anchor_mask}"
        )
        assert not _sig_ignored(cli_mask, number), (
            f"the probed CLI inherited signal {number} ignored instead of the "
            f"default disposition a subprocess would have had: {cli_mask}"
        )
    assert not _sig_ignored(anchor_mask, signal.SIGCHLD), (
        f"the anchor ignores SIGCHLD, so the status it exists to collect is "
        f"reaped out from under it: {anchor_mask}"
    )
    assert not _sig_ignored(cli_mask, signal.SIGCHLD), cli_mask


_PARENT_DEATH_DRIVER = '''\
"""A real supervisor for one anchored probe, written to be killed mid-probe.

It launches the anchor and then does nothing at all, so the only way it ever
ends is the ``SIGKILL`` the test sends it. It never closes the anchor, which is
the point: what tears the group down has to be the parent's death itself.
"""
import os
import sys
import time
from pathlib import Path

witness, anchor_pidfile, cli, scratch = sys.argv[1:5]

# Every process-group signal this supervisor aims by number, recorded on disk
# *before* it is delivered: a SIGKILL can stop the next line from running, but
# it cannot unwrite the last one.
_real_killpg, _real_kill = os.killpg, os.kill


def _record(target, sig):
    with open(witness, "a", encoding="ascii") as handle:
        handle.write("%d %d\\n" % (target, sig))


def _killpg(pgid, sig):
    _record(pgid, sig)
    return _real_killpg(pgid, sig)


def _kill(pid, sig):
    if pid <= 0:  # a group signal wearing ``kill``'s clothes
        _record(pid, sig)
    return _real_kill(pid, sig)


os.killpg, os.kill = _killpg, _kill

from agent_run_supervisor.native_acp import runtime_binding as rb

anchor = rb._ProbeAnchor.launch([cli], cwd=scratch)
Path(anchor_pidfile).write_text(str(anchor.pid), encoding="ascii")
time.sleep(120)
'''


def test_probe_group_dies_with_its_supervisor_even_with_no_reader_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B6: losing the supervisor is a path the anchor *covers*, so it must finish.

    The anchor's own account of itself is that this process dying is handled —
    the kernel closes the write end of the control pipe, the anchor reads EOF,
    and the group tears itself down unattended. That claim is only true if
    teardown can run with nobody left to talk to, because the same death closes
    the status *reader* in the same instant. Teardown's first act is to report
    the group it is about to signal into exactly that pipe, so the report is
    guaranteed to fail on the one path it matters most on. Made fatal, it kills
    the anchor at the write and leaves the group it was holding running; made an
    ``EPIPE``, it is swallowed on the way to ``killpg(0, ...)``.

    This cannot be observed from inside a supervisor that survives, so none does.
    A separate real driver launches the anchor over a real CLI which forks a
    descendant with its stdio redirected away from the probe's pipes — nothing
    but a signal to the group can reach that descendant — and then the driver is
    ``SIGKILL``ed where it can neither clean up nor be asked to.

    Distinct from the anchor being destroyed from outside, which the anchor does
    not claim to cover and which fails closed below instead.
    """
    import subprocess
    import sys

    if _running_since(os.getpid()) is None:  # pragma: no cover - host-dependent
        pytest.skip("/proc is unavailable; cannot observe process liveness")

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    witness = tmp_path / "group-signals"
    anchor_pidfile = tmp_path / "anchor.pid"
    cli_pidfile = tmp_path / "cli.pid"
    descendant_pidfile = tmp_path / "descendant.pid"
    cli = tmp_path / "surviving-cli"
    cli.write_text(
        "#!/bin/sh\n"
        # Redirected away from the probe's pipes on purpose. Those pipes die with
        # the supervisor, so a descendant still holding them could exit on its
        # own and prove nothing; this one can only be reached by a signal aimed
        # at the group, which is the single thing under test here.
        f"sleep 120 >/dev/null 2>&1 &\nprintf '%s\\n' \"$!\" > '{descendant_pidfile}'\n"
        f"printf '%s\\n' \"$$\" > '{cli_pidfile}'\n"
        # ``exec`` keeps the probe tree to exactly the three pids recorded here,
        # so this test's own cleanup can be by pid and never by group id.
        "exec sleep 120\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    driver_file = tmp_path / "driver.py"
    driver_file.write_text(_PARENT_DEATH_DRIVER, encoding="utf-8")
    driver_log = tmp_path / "driver.err"

    bystander = subprocess.Popen(  # noqa: S603 - test fixture, fixed argv
        ["/bin/sh", "-c", "exec sleep 30"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(entry for entry in sys.path if entry)
    caller_group = os.getpgrp()
    signals = _GroupSignalWitness(monkeypatch)
    before = _open_fds()
    with driver_log.open("wb") as errors:
        driver = subprocess.Popen(  # noqa: S603 - test fixture, fixed argv
            [
                sys.executable,
                str(driver_file),
                str(witness),
                str(anchor_pidfile),
                str(cli),
                str(scratch),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=errors,
            env=env,
        )
    live: dict[str, tuple[int, str | None]] = {}
    try:
        recorded = (anchor_pidfile, cli_pidfile, descendant_pidfile)
        deadline = time.monotonic() + 20.0
        while not all(path.exists() for path in recorded):
            assert driver.poll() is None, (
                f"the supervisor exited ({driver.returncode}) before its probe was "
                f"live: {driver_log.read_text(encoding='utf-8', errors='replace')}"
            )
            assert time.monotonic() < deadline, "the probe tree never became live"
            time.sleep(0.02)
        anchor, cli_pid, descendant = (_recorded_pid(path) for path in recorded)
        live = {
            role: (pid, _running_since(pid))
            for role, pid in (
                ("anchor", anchor),
                ("cli", cli_pid),
                ("descendant", descendant),
            )
        }
        assert all(since for _pid, since in live.values()), (
            f"the probe tree was not live to begin with: {live}"
        )
        assert len({pid for pid, _since in live.values()}) == 3, live
        # Nothing the descendant holds can end it, so surviving the supervisor
        # would be a statement about the group signal and about nothing else.
        # Waited for, because the redirection is the forked child's own doing and
        # the shell that recorded its pid had already moved on.
        deadline = time.monotonic() + 5.0
        while True:
            stdio = [os.readlink(f"/proc/{descendant}/fd/{fd}") for fd in (1, 2)]
            if stdio == [os.devnull, os.devnull]:
                break
            assert time.monotonic() < deadline, (
                f"the descendant never let go of the probe's own pipes: {stdio}"
            )
            time.sleep(0.02)

        # The death itself: no unwinding, no close, and the control writer and
        # the status reader go together, in the same instant.
        os.kill(driver.pid, signal.SIGKILL)
        assert driver.wait(timeout=10.0) == -signal.SIGKILL

        deadline = time.monotonic() + 10.0
        while True:
            survivors = {
                role: pid
                for role, (pid, since) in live.items()
                if _still_running(pid, since)
            }
            if not survivors:
                break
            assert time.monotonic() < deadline, (
                "the anchor did not tear its own group down after its supervisor "
                f"died, so the probe outlived the process that owned it: {survivors}"
            )
            time.sleep(0.05)
        # An unrelated live group, in its own session, is untouched by all of it.
        assert _running_since(bystander.pid) is not None
    finally:
        # Identity-safe, and by pid: a starttime that still matches is the same
        # process, and one that does not is somebody else's and is never signalled.
        for pid, since in live.values():
            if _still_running(pid, since):
                os.kill(pid, signal.SIGKILL)
        if driver.poll() is None:  # pragma: no cover - only if the test bailed early
            os.kill(driver.pid, signal.SIGKILL)
        driver.wait(timeout=10.0)
        if bystander.poll() is None:
            os.kill(bystander.pid, signal.SIGKILL)
        bystander.wait(timeout=10.0)

    assert not witness.exists(), (
        "the supervisor aimed a process-group signal by number rather than "
        f"letting the anchor signal its own: {witness.read_text(encoding='ascii')!r}"
    )
    assert signals.calls == [], f"CALLS={signals.calls}"
    assert os.getpgrp() == caller_group  # never the caller's own group
    assert _open_fds() == before, "the parent-death path leaked a descriptor"


def test_probe_contains_its_group_even_after_its_anchor_is_destroyed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B6: the anchor is killed and reaped externally — refuse, and aim nothing.

    This is the one case the lifetime argument does not cover, so it is the case
    that must fail closed. The anchor is ``SIGKILL``ed from outside and reaped
    behind ``Popen``'s back, exactly as an external reaper would: its group id is
    now a number nothing guarantees, and no observation this process could take
    would change that. So no number is aimed at — the probe refuses with the
    status it never received, promptly, and the descendant left behind is
    reported by this test rather than papered over.
    """
    import dataclasses

    if _running_since(os.getpid()) is None:  # pragma: no cover - host-dependent
        pytest.skip("/proc is unavailable; cannot observe anchor liveness")

    facts = tmp_path / "facts"
    script = _facts_cli(tmp_path, name="orphaned-cli", facts=facts, exit_code=7)
    rule = dataclasses.replace(
        OPENCODE_NATIVE_ACP.contract.version_probe, timeout_seconds=20.0
    )
    witness = _GroupSignalWitness(monkeypatch)

    before = _open_fds()
    anchor = rb._ProbeAnchor.launch([str(script)], cwd=str(tmp_path))
    try:
        _recorded_facts(facts)  # the CLI really ran under the anchor
        os.kill(anchor.pid, signal.SIGKILL)  # not this code's doing
        os.waitpid(anchor.pid, 0)  # ... and reaped behind ``Popen``'s back

        started = time.monotonic()
        with pytest.raises(rb.BindingRefusal) as excinfo:
            anchor.capture(limit=rule.max_output_bytes, timeout=rule.timeout_seconds)
        elapsed = time.monotonic() - started
    finally:
        anchor.close()

    assert excinfo.value.rule == "PROBE_STATUS_LOST"
    # Bounded: a status that can never arrive must not be waited out.
    assert elapsed < 5.0 < rule.timeout_seconds
    assert witness.calls == [], (
        "a group id with nothing holding it was still aimed at: "
        f"CALLS={witness.calls}"
    )
    assert _open_fds() == before, "the anchor did not give every descriptor back"


def test_probe_teardown_aims_at_no_group_on_any_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B6 isolation, stated once for every exit the probe has.

    The caller's own group, pid 1, an empty or recycled group id, an unrelated
    live group: none of them can be signalled, because no group id is ever an
    argument. The unrelated live group here is a real one, and it is still
    running afterwards.
    """
    import dataclasses
    import subprocess

    script = _facts_cli(tmp_path, name="ok-cli", facts=tmp_path / "ok.facts")
    failing = _facts_cli(
        tmp_path, name="bad-cli", facts=tmp_path / "bad.facts", exit_code=7
    )
    silent = tmp_path / "silent-cli"
    silent.write_text("#!/bin/sh\nprintf 'nothing here\\n'\n", encoding="utf-8")
    silent.chmod(0o755)
    hanging = tmp_path / "hanging-cli"
    hanging.write_text(
        "#!/bin/sh\nprintf '%s\\n' '1.18.5'\nsleep 60\n", encoding="utf-8"
    )
    hanging.chmod(0o755)

    bystander = subprocess.Popen(  # noqa: S603 - test fixture, fixed argv
        ["/bin/sh", "-c", "exec sleep 30"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    witness = _GroupSignalWitness(monkeypatch)
    rule = OPENCODE_NATIVE_ACP.contract.version_probe
    quick = dataclasses.replace(rule, timeout_seconds=0.5)
    before = _open_fds()
    try:
        assert rb.probe_cli_version(executable=str(script), rule=rule) == "1.18.5"
        for executable, expected, used in (
            (failing, "PROBE_FAILED", rule),
            (silent, "PROBE_UNPARSABLE", rule),
            (tmp_path / "absent-cli", "PROBE_FAILED", rule),
            (hanging, "PROBE_TIMEOUT", quick),
        ):
            with pytest.raises(rb.BindingRefusal) as excinfo:
                rb.probe_cli_version(executable=str(executable), rule=used)
            assert excinfo.value.rule == expected, executable

        assert witness.calls == [], f"CALLS={witness.calls}"
        assert _open_fds() == before, "a probe path leaked a descriptor"
        # Nothing of this process's own was in the blast radius, on any path,
        # and no probe process was left behind unreaped: the bystander is the
        # only child this process still has, and it is alive rather than waited.
        assert _running_since(bystander.pid) is not None
        assert os.waitpid(-1, os.WNOHANG) == (0, 0)
    finally:
        os.kill(bystander.pid, signal.SIGKILL)
        bystander.wait()


# ---------------------------------------------------------------------------
# C4 — acceptance provenance must be well formed (and still never authorizes)
# ---------------------------------------------------------------------------

_GOOD_PROVENANCE = {
    "created_at": "2026-07-26T09:00:00+08:00",
    "accepted_by": "operator",
    "accepted_at": "2026-07-26T09:00:00+08:00",
    "acceptance_receipt": {"ref": "receipt:local", "sha256": "a" * 64},
}


def _provenance(**overrides: Any) -> dict[str, Any]:
    payload = json.loads(json.dumps(_GOOD_PROVENANCE))
    for key, value in overrides.items():
        if value is _OMIT:
            payload.pop(key, None)
        else:
            payload[key] = value
    return payload


class _Omit:
    pass


_OMIT = _Omit()


@pytest.mark.parametrize(
    ("provenance", "rule"),
    [
        ({}, "PROVENANCE_FIELD_MISSING"),
        (_provenance(acceptance_receipt=_OMIT), "PROVENANCE_FIELD_MISSING"),
        (_provenance(created_at=_OMIT), "PROVENANCE_FIELD_MISSING"),
        (_provenance(accepted_by=_OMIT), "PROVENANCE_FIELD_MISSING"),
        (_provenance(acceptance_receipt=None), "PROVENANCE_FIELD_TYPE"),
        (_provenance(accepted_at=17), "PROVENANCE_FIELD_TYPE"),
        (_provenance(accepted_by=""), "PROVENANCE_FIELD_TYPE"),
        (_provenance(created_at="x" * 300), "PROVENANCE_FIELD_TYPE"),
        (
            _provenance(acceptance_receipt={"ref": "receipt:local"}),
            "RECEIPT_FIELD_MISSING",
        ),
        (
            _provenance(acceptance_receipt={"ref": "receipt:local", "sha256": "zz"}),
            "RECEIPT_FIELD_TYPE",
        ),
        (
            _provenance(acceptance_receipt={"ref": 3, "sha256": "a" * 64}),
            "RECEIPT_FIELD_TYPE",
        ),
    ],
)
def test_malformed_provenance_is_refused(
    tmp_path: Path, provenance: dict[str, Any], rule: str
) -> None:
    root = build_root(tmp_path, manifest_overrides={"provenance": provenance})
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == rule


def test_well_formed_provenance_still_never_authorizes_a_mismatched_contract(
    tmp_path: Path,
) -> None:
    """C4/C10 nuance: a perfect receipt is evidence, never an identity gate."""
    root = build_root(
        tmp_path,
        contract_identity={
            "profile_id": OPENCODE_NATIVE_ACP.profile_id,
            "profile_revision": OPENCODE_NATIVE_ACP.revision + 1,
            "adapter_contract_hash": OPENCODE_NATIVE_ACP.adapter_contract_hash(),
        },
        manifest_overrides={"provenance": _provenance()},
    )
    with pytest.raises(rb.BindingRefusal) as excinfo:
        _resolve(root)
    assert excinfo.value.rule == "CONTRACT_IDENTITY_MISMATCH"

"""D10(a) spawn-boundary attestation: descriptor pinning, hash-through-inode
identity, credential-root structure, project-config closure, the deterministic
in-window race seam, and the post-hook liveness/absence recheck.

Every artifact this suite pins, tampers, swaps, retargets, or deletes is a
per-test temporary copy staged under ``tmp_path`` (r8 fixture-isolation rule).
No repository fixture, installed adapter tree, frozen Node copy, real CLI, or
real credential root is opened for write, renamed, chmodded, or deleted here,
and no real credential bytes are ever read.
"""

from __future__ import annotations

import ast
import dataclasses
import errno
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_run_supervisor.native_acp import attestation as attestation_module
from agent_run_supervisor.native_acp.attestation import (
    CREDENTIAL_ROOT_VIOLATION,
    PROJECT_CONFIG_LAYER_PRESENT,
    RUNTIME_IDENTITY_MISMATCH,
    ArtifactClosure,
    AttestationRefusal,
    SealedRuntimeIdentity,
    attest_spawn_boundary,
    project_config_closure,
)
from agent_run_supervisor.native_acp.runtime_binding import package_tree_digest
from agent_run_supervisor.native_acp.spec import ResolvedLaunchSpec

from . import binding_fixtures as bf

# Placeholder credential bytes: synthetic, never a real credential value, and
# asserted absent from every persisted report.
AUTH_PLACEHOLDER = b'{"placeholder":"not-a-real-credential"}'
CANONICAL_CODEX_CONFIG = '{"features":{"use_legacy_landlock":true}}'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class Fixture:
    """Private per-test copies of every attested artifact."""

    node: Path
    entry: Path
    cli: Path
    cli_target: Path
    package_root: Path
    cli_interpreter: Path
    cred_root: Path
    auth: Path
    workspace: Path
    run_dir: Path
    expected: SealedRuntimeIdentity
    fixed_env: dict[str, str]
    launch: ResolvedLaunchSpec

    def report(self) -> dict:
        return json.loads((self.run_dir / "attestation.json").read_text(encoding="utf-8"))

    def rows(self) -> dict[str, dict]:
        return {row["name"]: row for row in self.report()["checks"]}

    def reseal(self, **overrides) -> SealedRuntimeIdentity:
        import dataclasses as _dc

        return _dc.replace(self.expected, **overrides)


def _closure(package_root: Path, launcher: Path, interpreter: Path) -> ArtifactClosure:
    return ArtifactClosure(
        kind="package_tree",
        path=str(launcher),
        sha256=_sha256(launcher),
        version="1.0.0",
        package_root=str(package_root),
        tree_sha256=package_tree_digest(package_root),
        interpreter_path=str(interpreter),
        interpreter_sha256=_sha256(interpreter),
    )


def _arrange(tmp_path: Path, *, cwd: Path | None = None) -> Fixture:
    stage = tmp_path / "stage"
    stage.mkdir(parents=True)
    node = stage / "node"
    node.write_bytes(b"#!/bin/false\n# private node copy\n")
    entry = stage / "index.js"
    entry.write_bytes(b"// private adapter entry copy\n")
    # The downstream CLI is a package closure: a launcher plus the sibling code
    # it loads plus its required interpreter. A launcher hash alone would not
    # freeze the siblings (C5).
    package_root = stage / "codex-pkg"
    (package_root / "lib").mkdir(parents=True)
    (package_root / "lib" / "sibling.js").write_bytes(b"// sibling code\n")
    cli = package_root / "codex"
    cli.write_bytes(b"# private cli copy\n")
    cli_target = cli
    cli_interpreter = stage / "cli-node"
    cli_interpreter.write_bytes(b"#!/bin/false\n# private cli interpreter\n")

    cred_root = tmp_path / "codex-home"
    cred_root.mkdir(mode=0o700)
    os.chmod(cred_root, 0o700)
    auth = cred_root / "auth.json"
    auth.write_bytes(AUTH_PLACEHOLDER)
    os.chmod(auth, 0o600)

    workspace = cwd if cwd is not None else tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    bf.harden_tree(stage)

    expected = SealedRuntimeIdentity(
        launch_kind="wrapped_acp",
        agent_info_name="@example/private-adapter",
        agent_info_version="1.0.0",
        protocol_version="1",
        cli=_closure(package_root, cli, cli_interpreter),
        cli_path_env="CODEX_PATH",
        node_path=str(node),
        node_sha256=_sha256(node),
        adapter_entry_path=str(entry),
        adapter_entry_sha256=_sha256(entry),
        credential_root_env="CODEX_HOME",
        credential_root_path=str(cred_root),
        project_config_relpath=".codex/config.toml",
    )
    fixed_env = {
        "CODEX_HOME": str(cred_root),
        "CODEX_PATH": str(cli),
        "CODEX_CONFIG": CANONICAL_CODEX_CONFIG,
        "INITIAL_AGENT_MODE": "read-only",
        "NO_BROWSER": "1",
    }
    launch = ResolvedLaunchSpec(
        executable=str(node),
        argv=(str(node), str(entry)),
        env_allowlist=("HOME", "PATH"),
        credential_refs=("codex-home-auth",),
        profile_id="private-profile-1.0",
        profile_revision=1,
        profile_hash="0" * 64,
        config_schema_hash="1" * 64,
    )
    return Fixture(
        node=node,
        entry=entry,
        cli=cli,
        cli_target=cli_target,
        package_root=package_root,
        cli_interpreter=cli_interpreter,
        cred_root=cred_root,
        auth=auth,
        workspace=workspace,
        run_dir=run_dir,
        expected=expected,
        fixed_env=fixed_env,
        launch=launch,
    )


def _reclose(fixture: Fixture) -> SealedRuntimeIdentity:
    """Re-seal the CLI closure after a test mutated the package tree."""
    return fixture.reseal(
        cli=_closure(fixture.package_root, fixture.cli, fixture.cli_interpreter)
    )


def _attest(
    fixture: Fixture,
    *,
    launch: ResolvedLaunchSpec | None = None,
    fixed_env: dict[str, str] | None = None,
    expected: SealedRuntimeIdentity | None = None,
):
    return attest_spawn_boundary(
        expected=expected if expected is not None else fixture.expected,
        launch=launch if launch is not None else fixture.launch,
        fixed_env=fixed_env if fixed_env is not None else fixture.fixed_env,
        effective_cwd=str(fixture.workspace),
        run_dir=fixture.run_dir,
        ownership=bf.ownership(),
    )


def _spawn_guard(monkeypatch: pytest.MonkeyPatch) -> list:
    """Records any spawn attempt; the attestation path must never spawn."""
    from agent_run_supervisor import managed_process as managed_process_module

    calls: list = []

    async def recorder(**kwargs):
        calls.append(kwargs)
        raise AssertionError("attestation must refuse before any spawn")

    monkeypatch.setattr(managed_process_module, "spawn_managed_process", recorder)
    return calls


def _refusal(fixture: Fixture, **kwargs) -> AttestationRefusal:
    with pytest.raises(AttestationRefusal) as err:
        _attest(fixture, **kwargs)
    return err.value


# -- clean boundary ----------------------------------------------------------


def test_clean_boundary_passes_and_returns_pinned_exec_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _spawn_guard(monkeypatch)
    fixture = _arrange(tmp_path)
    result = _attest(fixture)
    try:
        report = fixture.report()
        assert report["pass"] is True
        assert all(row["passed"] for row in report["checks"])
        names = [row["name"] for row in report["checks"]]
        for required in (
            "node_credential_alias",
            "adapter_entry_credential_alias",
            "cli_credential_alias",
            "node_sha256",
            "adapter_entry_sha256",
            "cli_sha256",
            "credential_root_structure",
            "auth_json_structure",
            "config_toml_absent",
            "project_config_closure",
            "node_binding_lost",
            "adapter_entry_binding_lost",
            "cli_binding_lost",
            "credential_root_binding_lost",
            "auth_json_binding_lost",
            "config_toml_absence_recheck",
            "project_config_closure_recheck",
        ):
            assert required in names, required
        node_stat = os.stat(fixture.node)
        pinned = os.fstat(result.exec_fd)
        assert (pinned.st_dev, pinned.st_ino) == (node_stat.st_dev, node_stat.st_ino)
        binding = report["binding"]
        assert binding["node"]["dev"] == node_stat.st_dev
        assert binding["node"]["ino"] == node_stat.st_ino
        assert binding["node"]["recheck_passed"] is True
    finally:
        os.close(result.exec_fd)
    assert spawns == []


# -- D11a project-config closure walk ---------------------------------------


def test_project_config_walk_covers_cwd_to_filesystem_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "a" / "b" / "c"
    cwd.mkdir(parents=True)
    probed: list[str] = []
    real_lexists = os.path.lexists

    def spy(path):
        probed.append(str(path))
        return False

    monkeypatch.setattr(os.path, "lexists", spy)
    assert project_config_closure(str(cwd)) is None
    monkeypatch.setattr(os.path, "lexists", real_lexists)

    expected_chain = []
    walker = cwd
    while True:
        expected_chain.append(str(walker / ".codex" / "config.toml"))
        if walker.parent == walker:
            break
        walker = walker.parent
    assert probed == expected_chain
    assert probed[-1] == str(Path("/") / ".codex" / "config.toml")


def test_project_config_at_workspace_root_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _spawn_guard(monkeypatch)
    fixture = _arrange(tmp_path)
    layer = fixture.workspace / ".codex"
    layer.mkdir()
    (layer / "config.toml").write_text("model = 'poison'\n", encoding="utf-8")

    refusal = _refusal(fixture)
    assert refusal.code == PROJECT_CONFIG_LAYER_PRESENT
    assert refusal.failing_check == "project_config_closure"
    row = fixture.rows()["project_config_closure"]
    assert row["passed"] is False
    assert row["observed"] == str(layer / "config.toml")
    assert spawns == []


def test_project_config_above_workspace_root_refused(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    cwd = parent / "ws"
    fixture = _arrange(tmp_path, cwd=cwd)
    layer = parent / ".codex"
    layer.mkdir()
    (layer / "config.toml").write_text("model = 'poison'\n", encoding="utf-8")

    refusal = _refusal(fixture)
    assert refusal.code == PROJECT_CONFIG_LAYER_PRESENT
    assert fixture.rows()["project_config_closure"]["observed"] == str(
        layer / "config.toml"
    )


def test_project_config_symlinked_codex_dir_refused(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "config.toml").write_text("model = 'poison'\n", encoding="utf-8")
    (fixture.workspace / ".codex").symlink_to(elsewhere, target_is_directory=True)

    refusal = _refusal(fixture)
    assert refusal.code == PROJECT_CONFIG_LAYER_PRESENT
    assert fixture.rows()["project_config_closure"]["passed"] is False


def test_project_config_symlink_config_toml_refused(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    layer = fixture.workspace / ".codex"
    layer.mkdir()
    # A broken symlink still counts: lexists, not exists, is the predicate.
    (layer / "config.toml").symlink_to(tmp_path / "nowhere.toml")

    refusal = _refusal(fixture)
    assert refusal.code == PROJECT_CONFIG_LAYER_PRESENT
    assert fixture.rows()["project_config_closure"]["observed"] == str(
        layer / "config.toml"
    )


# -- D11b credential-root structure -----------------------------------------


def test_credential_root_symlink_refused(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    real_home = tmp_path / "real-home"
    real_home.mkdir(mode=0o700)
    os.chmod(real_home, 0o700)
    (real_home / "auth.json").write_bytes(AUTH_PLACEHOLDER)
    os.chmod(real_home / "auth.json", 0o600)
    swapped = tmp_path / "swapped-home"
    fixture.cred_root.rename(swapped)
    Path(fixture.fixed_env["CODEX_HOME"]).symlink_to(
        real_home, target_is_directory=True
    )

    refusal = _refusal(fixture)
    assert refusal.code == CREDENTIAL_ROOT_VIOLATION
    assert refusal.failing_check == "credential_root_structure"
    assert fixture.rows()["credential_root_structure"]["observed"] == "symlink"


@pytest.mark.parametrize("mode", [0o750, 0o755, 0o770])
def test_credential_root_mode_not_0700_refused(tmp_path: Path, mode: int) -> None:
    fixture = _arrange(tmp_path)
    os.chmod(fixture.cred_root, mode)

    refusal = _refusal(fixture)
    assert refusal.code == CREDENTIAL_ROOT_VIOLATION
    row = fixture.rows()["credential_root_structure"]
    assert row["expected"] == "0o700"
    assert row["observed"] == oct(mode)


def test_credential_root_wrong_owner_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _arrange(tmp_path)
    monkeypatch.setattr(attestation_module, "_effective_uid", lambda: os.geteuid() + 1)

    refusal = _refusal(fixture)
    assert refusal.code == CREDENTIAL_ROOT_VIOLATION
    assert fixture.rows()["credential_root_structure"]["passed"] is False


def test_auth_json_symlink_refused(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    target = tmp_path / "elsewhere-auth.json"
    target.write_bytes(AUTH_PLACEHOLDER)
    os.chmod(target, 0o600)
    fixture.auth.unlink()
    fixture.auth.symlink_to(target)

    refusal = _refusal(fixture)
    assert refusal.code == CREDENTIAL_ROOT_VIOLATION
    assert refusal.failing_check == "auth_json_structure"
    assert fixture.rows()["auth_json_structure"]["observed"] == "ELOOP"


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660])
def test_auth_json_mode_not_0600_refused(tmp_path: Path, mode: int) -> None:
    fixture = _arrange(tmp_path)
    os.chmod(fixture.auth, mode)

    refusal = _refusal(fixture)
    assert refusal.code == CREDENTIAL_ROOT_VIOLATION
    row = fixture.rows()["auth_json_structure"]
    assert row["expected"] == "0o600"
    assert row["observed"] == oct(mode)


def test_auth_json_missing_refused(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    fixture.auth.unlink()

    refusal = _refusal(fixture)
    assert refusal.code == CREDENTIAL_ROOT_VIOLATION
    assert fixture.rows()["auth_json_structure"]["observed"] == "ENOENT"


def test_config_toml_present_refused(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    # An ambient config.toml in the credential root would merge into and widen
    # the frozen CODEX_CONFIG.
    (fixture.cred_root / "config.toml").write_text("[features]\n", encoding="utf-8")

    refusal = _refusal(fixture)
    assert refusal.code == CREDENTIAL_ROOT_VIOLATION
    assert refusal.failing_check == "config_toml_absent"
    assert fixture.rows()["config_toml_absent"]["passed"] is False


# -- runtime identity --------------------------------------------------------


def test_node_hash_mismatch_refused(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    fixture.node.write_bytes(b"#!/bin/false\n# tampered private node copy\n")

    refusal = _refusal(fixture)
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert refusal.failing_check == "node_sha256"
    row = fixture.rows()["node_sha256"]
    assert row["expected"] == fixture.expected.node_sha256
    assert row["observed"] == _sha256(fixture.node)
    assert row["observed"] != row["expected"]


def test_adapter_entry_hash_mismatch_refused(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    fixture.entry.write_bytes(b"// tampered private adapter entry copy\n")

    refusal = _refusal(fixture)
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert refusal.failing_check == "adapter_entry_sha256"
    assert fixture.rows()["adapter_entry_sha256"]["passed"] is False


def test_cli_replaced_by_a_symlink_is_refused_at_the_pin(tmp_path: Path) -> None:
    # A Binding names an immutable versioned path and the Binding reader
    # already refused a symlinked artifact, so a symlink appearing here is a
    # swap between admission and spawn — refused before any hash row.
    fixture = _arrange(tmp_path)
    other = tmp_path / "stage" / "codex-other"
    other.write_bytes(b"# a different cli binary\n")
    fixture.cli.unlink()
    fixture.cli.symlink_to(other)

    refusal = _refusal(fixture)
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert refusal.failing_check == "cli_pin"
    assert fixture.rows()["cli_pin"]["passed"] is False
    assert "cli_sha256" not in fixture.rows()


def test_cli_content_drift_refused_at_the_hash_row(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    fixture.cli.write_bytes(b"# a different cli binary\n")

    refusal = _refusal(fixture)
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert refusal.failing_check == "cli_sha256"
    assert fixture.rows()["cli_sha256"]["observed"] == _sha256(fixture.cli)


# -- credential aliasing (no hashed artifact may be the credential file) ------


def _hash_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records every artifact whose content-hash function is invoked."""
    real = attestation_module._AttestationState.hash_artifact
    hashed: list[str] = []

    def spy(self, artifact, *args, **kwargs):
        hashed.append(artifact)
        return real(self, artifact, *args, **kwargs)

    monkeypatch.setattr(
        attestation_module._AttestationState, "hash_artifact", spy
    )
    return hashed


def _assert_no_credential_derivation(
    fixture: Fixture, refusal: AttestationRefusal
) -> None:
    """Neither the credential bytes nor their digest may survive anywhere."""
    digest = hashlib.sha256(AUTH_PLACEHOLDER).hexdigest()
    raw = (fixture.run_dir / "attestation.json").read_bytes()
    assert AUTH_PLACEHOLDER not in raw
    assert digest.encode() not in raw
    assert AUTH_PLACEHOLDER.decode() not in refusal.message
    assert digest not in refusal.message


def test_cli_hardlinked_to_credential_file_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A hardlink shares the inode under a different name, so the CLI pin lands
    # on CODEX_HOME/auth.json without any symlink for the pin to refuse: the
    # identity hash would otherwise read credential bytes.
    spawns = _spawn_guard(monkeypatch)
    hashed = _hash_spy(monkeypatch)
    fixture = _arrange(tmp_path)
    fixture.cli.unlink()
    os.link(fixture.auth, fixture.cli)

    refusal = _refusal(fixture)
    assert refusal.code == CREDENTIAL_ROOT_VIOLATION
    assert refusal.failing_check == "cli_credential_alias"
    assert hashed == []
    _assert_no_credential_derivation(fixture, refusal)
    rows = fixture.rows()
    assert rows["cli_credential_alias"]["passed"] is False
    assert "cli_sha256" not in rows
    assert spawns == []


def test_node_hardlinked_to_credential_file_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A hardlink shares the inode under a different name: pathname comparison
    # would miss it, inode comparison cannot.
    spawns = _spawn_guard(monkeypatch)
    hashed = _hash_spy(monkeypatch)
    fixture = _arrange(tmp_path)
    fixture.node.unlink()
    os.link(fixture.auth, fixture.node)

    refusal = _refusal(fixture)
    assert refusal.code == CREDENTIAL_ROOT_VIOLATION
    assert refusal.failing_check == "node_credential_alias"
    assert hashed == []
    _assert_no_credential_derivation(fixture, refusal)
    assert spawns == []


def test_adapter_entry_hardlinked_to_credential_file_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _spawn_guard(monkeypatch)
    hashed = _hash_spy(monkeypatch)
    fixture = _arrange(tmp_path)
    fixture.entry.unlink()
    os.link(fixture.auth, fixture.entry)

    refusal = _refusal(fixture)
    assert refusal.code == CREDENTIAL_ROOT_VIOLATION
    assert refusal.failing_check == "adapter_entry_credential_alias"
    assert hashed == []
    _assert_no_credential_derivation(fixture, refusal)
    assert spawns == []


def test_credential_alias_through_symlinked_root_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The declared root is itself retargeted, so the credential file is only
    # reachable by following the root symlink. The structural identification
    # must still resolve it before any hash runs — the credential-root
    # structure row refuses the symlinked root, but only *after* hashing.
    spawns = _spawn_guard(monkeypatch)
    hashed = _hash_spy(monkeypatch)
    fixture = _arrange(tmp_path)
    elsewhere = tmp_path / "relocated-home"
    elsewhere.mkdir(mode=0o700)
    os.chmod(elsewhere, 0o700)
    relocated_auth = elsewhere / "auth.json"
    relocated_auth.write_bytes(AUTH_PLACEHOLDER)
    os.chmod(relocated_auth, 0o600)
    fixture.auth.unlink()
    fixture.cred_root.rmdir()
    Path(fixture.fixed_env["CODEX_HOME"]).symlink_to(
        elsewhere, target_is_directory=True
    )
    fixture.cli.unlink()
    os.link(relocated_auth, fixture.cli)

    refusal = _refusal(fixture)
    assert refusal.code == CREDENTIAL_ROOT_VIOLATION
    assert refusal.failing_check == "cli_credential_alias"
    assert hashed == []
    _assert_no_credential_derivation(fixture, refusal)
    assert spawns == []


def test_benign_cli_drift_still_reaches_the_hash_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The alias guard must not swallow ordinary identity drift.
    hashed = _hash_spy(monkeypatch)
    fixture = _arrange(tmp_path)
    fixture.cli.write_bytes(b"# a different, non-credential cli binary\n")

    refusal = _refusal(fixture)
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert refusal.failing_check == "cli_sha256"
    assert hashed == ["node", "adapter_entry", "cli"]
    rows = fixture.rows()
    for name in (
        "node_credential_alias",
        "adapter_entry_credential_alias",
        "cli_credential_alias",
    ):
        assert rows[name]["passed"] is True, name


def test_credential_alias_rows_are_recorded_before_any_hash_row(
    tmp_path: Path,
) -> None:
    fixture = _arrange(tmp_path)
    result = _attest(fixture)
    os.close(result.exec_fd)

    names = [row["name"] for row in fixture.report()["checks"]]
    for artifact in ("node", "adapter_entry", "cli"):
        alias = names.index(f"{artifact}_credential_alias")
        assert alias < names.index("node_sha256"), artifact
        assert names.index(f"{artifact}_pin") < alias, artifact
    # Identification precedes hashing but never displaces the structure rows.
    assert names.index("cli_credential_alias") < names.index(
        "credential_root_structure"
    )


@pytest.mark.parametrize(
    "mutation, failing_check",
    [
        ("argv0", "argv_node_binding"),
        ("argv1", "argv_adapter_entry_binding"),
        ("env", "env_cli_path_binding"),
    ],
)
def test_argv_env_binding_mismatch_refused(
    tmp_path: Path, mutation: str, failing_check: str
) -> None:
    fixture = _arrange(tmp_path)
    launch = fixture.launch
    fixed_env = dict(fixture.fixed_env)
    if mutation == "argv0":
        launch = ResolvedLaunchSpec(
            **{
                **{
                    field: getattr(fixture.launch, field)
                    for field in ("executable", "env_allowlist", "credential_refs",
                                  "profile_id", "profile_revision", "profile_hash",
                                  "config_schema_hash")
                },
                "argv": ("/somewhere/else/node", str(fixture.entry)),
            }
        )
    elif mutation == "argv1":
        launch = ResolvedLaunchSpec(
            **{
                **{
                    field: getattr(fixture.launch, field)
                    for field in ("executable", "env_allowlist", "credential_refs",
                                  "profile_id", "profile_revision", "profile_hash",
                                  "config_schema_hash")
                },
                "argv": (str(fixture.node), "/somewhere/else/index.js"),
            }
        )
    else:
        fixed_env["CODEX_PATH"] = "/somewhere/else/codex"

    with pytest.raises(AttestationRefusal) as err:
        _attest(fixture, launch=launch, fixed_env=fixed_env)
    assert err.value.code == RUNTIME_IDENTITY_MISMATCH
    assert err.value.failing_check == failing_check
    assert fixture.rows()[failing_check]["passed"] is False


# -- missing / unreadable artifacts ------------------------------------------


def test_node_missing_refused(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    fixture.node.unlink()

    refusal = _refusal(fixture)
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert refusal.failing_check == "node_pin"
    assert fixture.rows()["node_pin"]["observed"] == "ENOENT"


def test_adapter_entry_missing_refused(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    fixture.entry.unlink()

    refusal = _refusal(fixture)
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert refusal.failing_check == "adapter_entry_pin"
    assert fixture.rows()["adapter_entry_pin"]["observed"] == "ENOENT"


def test_cli_missing_refused(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    fixture.cli_target.unlink()  # the configured path is now a dangling symlink

    refusal = _refusal(fixture)
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert refusal.failing_check == "cli_pin"
    assert fixture.rows()["cli_pin"]["observed"] == "ENOENT"


def test_artifact_unreadable_refused_with_errno_row(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    # O_PATH still pins a mode-000 file; the hashing reopen is what fails.
    os.chmod(fixture.entry, 0o000)
    try:
        refusal = _refusal(fixture)
    finally:
        os.chmod(fixture.entry, 0o600)
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert refusal.failing_check == "adapter_entry_sha256"
    report = fixture.report()
    assert report["pass"] is False
    row = fixture.rows()["adapter_entry_sha256"]
    assert row["observed"] == "EACCES"
    # No crash and no truncated artifact: the report is complete JSON with the
    # preceding rows intact.
    assert fixture.rows()["node_sha256"]["passed"] is True


def test_missing_o_path_support_refused_platform_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The gate never degrades to pathname trust.
    fixture = _arrange(tmp_path)
    monkeypatch.delattr(os, "O_PATH", raising=False)

    refusal = _refusal(fixture)
    assert refusal.failing_check == "platform_unsupported"
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH


# -- deterministic in-window races (hook → recheck) --------------------------


def _arm(monkeypatch: pytest.MonkeyPatch, hook) -> None:
    monkeypatch.setattr(attestation_module, "_POST_ATTESTATION_HOOK", hook)


def test_inwindow_node_path_swap_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _spawn_guard(monkeypatch)
    fixture = _arrange(tmp_path)
    replacement = tmp_path / "stage" / "node-evil"
    replacement.write_bytes(b"#!/bin/false\n# swapped-in node\n")

    def hook() -> None:
        os.replace(replacement, fixture.node)

    _arm(monkeypatch, hook)
    refusal = _refusal(fixture)
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert refusal.failing_check == "node_binding_lost"
    rows = fixture.rows()
    assert rows["node_binding_lost"]["passed"] is False
    assert rows["node_sha256"]["passed"] is True  # pre-hook rows all passed
    assert fixture.report()["binding"]["node"]["recheck_passed"] is False
    assert spawns == []


def test_inwindow_adapter_entry_swap_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _spawn_guard(monkeypatch)
    fixture = _arrange(tmp_path)
    replacement = tmp_path / "stage" / "index-evil.js"
    replacement.write_bytes(b"// swapped-in adapter entry\n")

    _arm(monkeypatch, lambda: os.replace(replacement, fixture.entry))
    refusal = _refusal(fixture)
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert refusal.failing_check == "adapter_entry_binding_lost"
    assert spawns == []


def test_inwindow_cli_retarget_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _spawn_guard(monkeypatch)
    fixture = _arrange(tmp_path)
    other = tmp_path / "stage" / "codex-evil"
    other.write_bytes(b"# retargeted cli\n")

    def hook() -> None:
        fixture.cli.unlink()
        fixture.cli.symlink_to(other)

    _arm(monkeypatch, hook)
    refusal = _refusal(fixture)
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert refusal.failing_check == "cli_binding_lost"
    assert spawns == []


def test_inwindow_credential_root_replaced_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _spawn_guard(monkeypatch)
    fixture = _arrange(tmp_path)
    replacement = tmp_path / "replacement-home"
    replacement.mkdir(mode=0o700)
    os.chmod(replacement, 0o700)
    (replacement / "auth.json").write_bytes(AUTH_PLACEHOLDER)
    os.chmod(replacement / "auth.json", 0o600)

    def hook() -> None:
        fixture.cred_root.rename(tmp_path / "displaced-home")
        replacement.rename(fixture.cred_root)

    _arm(monkeypatch, hook)
    refusal = _refusal(fixture)
    assert refusal.code == CREDENTIAL_ROOT_VIOLATION
    assert refusal.failing_check == "credential_root_binding_lost"
    assert fixture.report()["binding"]["credential_root"]["recheck_passed"] is False
    assert spawns == []


def test_inwindow_auth_json_replaced_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _spawn_guard(monkeypatch)
    fixture = _arrange(tmp_path)
    replacement = tmp_path / "replacement-auth.json"
    replacement.write_bytes(AUTH_PLACEHOLDER)
    os.chmod(replacement, 0o600)

    _arm(monkeypatch, lambda: os.replace(replacement, fixture.auth))
    refusal = _refusal(fixture)
    assert refusal.code == CREDENTIAL_ROOT_VIOLATION
    assert refusal.failing_check == "auth_json_binding_lost"
    assert spawns == []


def test_inwindow_credential_root_config_toml_inserted_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _spawn_guard(monkeypatch)
    fixture = _arrange(tmp_path)

    def hook() -> None:
        (fixture.cred_root / "config.toml").write_text("[features]\n", encoding="utf-8")

    _arm(monkeypatch, hook)
    refusal = _refusal(fixture)
    assert refusal.code == CREDENTIAL_ROOT_VIOLATION
    assert refusal.failing_check == "config_toml_absence_recheck"
    rows = fixture.rows()
    assert rows["config_toml_absence_recheck"]["passed"] is False
    for pre_hook in (
        "node_sha256",
        "adapter_entry_sha256",
        "cli_sha256",
        "credential_root_structure",
        "auth_json_structure",
        "config_toml_absent",
        "project_config_closure",
    ):
        assert rows[pre_hook]["passed"] is True, pre_hook
    assert spawns == []


def test_inwindow_project_config_inserted_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _spawn_guard(monkeypatch)
    fixture = _arrange(tmp_path)
    inserted = fixture.workspace / ".codex" / "config.toml"

    def hook() -> None:
        inserted.parent.mkdir()
        inserted.write_text("model = 'poison'\n", encoding="utf-8")

    _arm(monkeypatch, hook)
    refusal = _refusal(fixture)
    assert refusal.code == PROJECT_CONFIG_LAYER_PRESENT
    assert refusal.failing_check == "project_config_closure_recheck"
    rows = fixture.rows()
    assert rows["project_config_closure_recheck"]["observed"] == str(inserted)
    assert rows["project_config_closure"]["passed"] is True
    assert spawns == []


# -- hygiene, sanitization, and the public refusal type ----------------------


def test_report_is_sanitized_paths_hashes_modes_only(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    result = _attest(fixture)
    os.close(result.exec_fd)

    report = fixture.report()
    assert set(report) == {"schema_version", "pass", "checks", "binding"}
    assert report["schema_version"] == 1
    assert isinstance(report["pass"], bool)
    for row in report["checks"]:
        assert set(row) == {"name", "expected", "observed", "passed"}
        assert isinstance(row["name"], str)
        assert isinstance(row["passed"], bool)
        for key in ("expected", "observed"):
            assert row[key] is None or isinstance(row[key], str)
    for artifact, facts in report["binding"].items():
        assert isinstance(artifact, str)
        assert set(facts) == {"dev", "ino", "recheck_passed"}
        assert isinstance(facts["dev"], int) and not isinstance(facts["dev"], bool)
        assert isinstance(facts["ino"], int) and not isinstance(facts["ino"], bool)
        assert isinstance(facts["recheck_passed"], bool)

    raw = (fixture.run_dir / "attestation.json").read_bytes()
    # No credential bytes, no credential-file digest, no env values beyond the
    # two fixed paths already named by the expected identity.
    assert AUTH_PLACEHOLDER not in raw
    assert hashlib.sha256(AUTH_PLACEHOLDER).hexdigest().encode() not in raw
    assert CANONICAL_CODEX_CONFIG.encode() not in raw
    assert b"NO_BROWSER" not in raw
    assert b"INITIAL_AGENT_MODE" not in raw


def _recorded_open(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    opened: list[int] = []
    real_open = os.open

    def spy(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened.append(fd)
        return fd

    monkeypatch.setattr(os, "open", spy)
    return opened


def test_attestation_fds_closed_on_refusal_and_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    success = _arrange(tmp_path / "ok")
    opened = _recorded_open(monkeypatch)
    result = _attest(success)
    monkeypatch.undo()
    survivors = []
    for fd in set(opened):
        try:
            os.fstat(fd)
        except OSError as exc:
            assert exc.errno == errno.EBADF
        else:
            survivors.append(fd)
    assert survivors == [result.exec_fd]
    os.close(result.exec_fd)

    refused = _arrange(tmp_path / "bad")
    refused.node.write_bytes(b"tampered\n")
    opened = _recorded_open(monkeypatch)
    with pytest.raises(AttestationRefusal):
        _attest(refused)
    monkeypatch.undo()
    for fd in set(opened):
        with pytest.raises(OSError) as err:
            os.fstat(fd)
        assert err.value.errno == errno.EBADF


def test_refusal_exception_is_public_typed_with_code_and_failing_check(
    tmp_path: Path,
) -> None:
    fixture = _arrange(tmp_path)
    fixture.entry.write_bytes(b"// tampered\n")

    with pytest.raises(AttestationRefusal) as err:
        _attest(fixture)
    refusal = err.value
    assert isinstance(refusal.code, str) and refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert isinstance(refusal.failing_check, str)
    assert isinstance(refusal.message, str) and refusal.message
    assert str(refusal) == refusal.message
    row = fixture.rows()[refusal.failing_check]
    assert row["passed"] is False

    # The dependency direction is run_task → attestation and can never invert.
    source = Path(attestation_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(alias.name for alias in node.names)
    assert not {name for name in imported if "run_task" in name}
    assert not hasattr(attestation_module, "_PreDispatchFailure")


# -- generalized identity bindings (B3: Claude-shaped runtimes) ---------------


def _arrange_claude(tmp_path: Path, *, cwd: Path | None = None) -> Fixture:
    """A Claude-shaped fixture: CLI bound through its own fixed-env key, no
    ARS-managed credential root, and no project-config closure surface."""
    fixture = _arrange(tmp_path, cwd=cwd)
    fixture.expected = fixture.reseal(
        agent_info_name="@example/private-claude-adapter",
        agent_info_version="0.0.1",
        cli_path_env="CLAUDE_CODE_EXECUTABLE",
        credential_root_env=None,
        credential_root_path=None,
        project_config_relpath=None,
    )
    fixture.fixed_env = {
        "CLAUDE_CODE_EXECUTABLE": str(fixture.cli),
        "NO_BROWSER": "1",
    }
    return fixture


def test_claude_shaped_boundary_passes_without_a_credential_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _spawn_guard(monkeypatch)
    fixture = _arrange_claude(tmp_path)
    result = _attest(fixture)
    try:
        report = fixture.report()
        assert report["pass"] is True
        assert all(row["passed"] for row in report["checks"])
        rows = fixture.rows()
        # Artifact identity rows still run in full.
        for required in (
            "node_pin",
            "adapter_entry_pin",
            "cli_pin",
            "node_sha256",
            "adapter_entry_sha256",
            "cli_sha256",
            "argv_node_binding",
            "argv_adapter_entry_binding",
            "env_cli_path_binding",
            "node_binding_lost",
            "adapter_entry_binding_lost",
            "cli_binding_lost",
        ):
            assert required in rows, required
        # Absent surfaces are declared explicitly, never silently skipped.
        assert rows["credential_root_not_declared"]["observed"] == "not declared"
        assert rows["project_config_not_declared"]["observed"] == "not declared"
        assert "credential_root_structure" not in rows
        assert "project_config_closure" not in rows
        assert os.fstat(result.exec_fd).st_ino == os.stat(fixture.node).st_ino
    finally:
        os.close(result.exec_fd)
    assert spawns == []


def test_claude_shaped_cli_env_binding_is_the_declared_key(tmp_path: Path) -> None:
    fixture = _arrange_claude(tmp_path)
    # The Codex key is not a substitute: the CLI binding is refused when the
    # declared key is missing, so no PATH-resolved fallback CLI can be reached.
    refusal = _refusal(fixture, fixed_env={"CODEX_PATH": str(fixture.cli)})
    assert refusal.failing_check == "env_cli_path_binding"
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert fixture.rows()["env_cli_path_binding"]["passed"] is False


def test_claude_shaped_cli_env_pointing_elsewhere_is_refused(tmp_path: Path) -> None:
    fixture = _arrange_claude(tmp_path)
    other = tmp_path / "stage" / "other-claude"
    other.write_bytes(b"# a different cli\n")
    refusal = _refusal(
        fixture,
        fixed_env={"CLAUDE_CODE_EXECUTABLE": str(other), "NO_BROWSER": "1"},
    )
    assert refusal.failing_check == "env_cli_path_binding"


def test_claude_shaped_ignores_a_codex_project_config_layer(tmp_path: Path) -> None:
    # A profile that declares no project-config surface must not inherit the
    # Codex closure: the frozen session metadata + forced permission mode are
    # its proven defense, and the live canary passed with a hostile workspace
    # settings file present.
    fixture = _arrange_claude(tmp_path)
    layer = fixture.workspace / ".codex"
    layer.mkdir()
    (layer / "config.toml").write_text("[features]\n", encoding="utf-8")
    result = _attest(fixture)
    os.close(result.exec_fd)
    assert fixture.report()["pass"] is True


def test_claude_shaped_artifact_drift_still_refuses(tmp_path: Path) -> None:
    fixture = _arrange_claude(tmp_path)
    fixture.entry.write_bytes(b"// swapped adapter entry\n")
    refusal = _refusal(fixture)
    assert refusal.failing_check == "adapter_entry_sha256"
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH


def test_codex_shaped_rows_survive_the_binding_split(tmp_path: Path) -> None:
    # A Codex-shaped runtime keeps the exact legacy row set (nothing weakened,
    # bypassed, or skipped) and gains the artifact-trust and closure rows.
    fixture = _arrange(tmp_path)
    result = _attest(fixture)
    os.close(result.exec_fd)
    rows = fixture.rows()
    assert "credential_root_not_declared" not in rows
    assert "project_config_not_declared" not in rows
    for required in (
        "credential_root_structure",
        "auth_json_structure",
        "config_toml_absent",
        "project_config_closure",
        "credential_root_binding_lost",
        "auth_json_binding_lost",
        "config_toml_absence_recheck",
        "project_config_closure_recheck",
        "cli_artifact_trust",
        "cli_package_closure",
        "cli_package_closure_recheck",
        "cli_interpreter_sha256",
    ):
        assert required in rows, required
    # A sealed identity states every surface it declares explicitly: the
    # per-Run record is evidence, so nothing is inferred from a default.
    payload = fixture.expected.to_dict()
    assert payload["cli_path_env"] == "CODEX_PATH"
    assert payload["credential_root_env"] == "CODEX_HOME"
    assert payload["credential_root_path"] == str(fixture.cred_root)
    assert payload["project_config_relpath"] == ".codex/config.toml"


# -- C5/C10: artifact trust and package closure ------------------------------


def test_service_uid_owned_cli_is_refused(tmp_path: Path) -> None:
    """The artifact-trust row is re-proven at the boundary, not inherited."""
    fixture = _arrange(tmp_path)
    with pytest.raises(AttestationRefusal) as err:
        attest_spawn_boundary(
            expected=fixture.expected,
            launch=fixture.launch,
            fixed_env=fixture.fixed_env,
            effective_cwd=str(fixture.workspace),
            run_dir=fixture.run_dir,
            ownership=attestation_module.TrustedOwnership(
                trusted_uids=frozenset({0}), service_uid=os.getuid()
            ),
        )
    assert err.value.failing_check == "cli_artifact_trust"
    assert err.value.code == RUNTIME_IDENTITY_MISMATCH
    assert fixture.rows()["cli_artifact_trust"]["observed"] == "SERVICE_UID_WRITABLE"


def test_sibling_code_change_refused_by_the_package_closure(tmp_path: Path) -> None:
    # A launcher hash alone would pass here: the launcher is untouched and only
    # the sibling code it loads changed.
    fixture = _arrange(tmp_path)
    (fixture.package_root / "lib" / "sibling.js").write_bytes(b"// swapped\n")
    refusal = _refusal(fixture)
    assert refusal.failing_check == "cli_package_closure"
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH


def test_writable_package_root_refused_by_the_artifact_ancestor_walk(
    tmp_path: Path,
) -> None:
    # The package root is an ancestor of the launcher, so the ancestor walk
    # refuses it first — a stricter row, reached earlier, same conclusion.
    fixture = _arrange(tmp_path)
    fixture.package_root.chmod(0o777)
    refusal = _refusal(fixture)
    assert refusal.failing_check == "cli_artifact_trust"
    assert fixture.rows()["cli_artifact_trust"]["observed"] == "GROUP_OR_OTHER_WRITABLE"


def test_writable_sibling_directory_refused_by_the_closure(tmp_path: Path) -> None:
    # ``lib/`` is not an ancestor of the launcher, so only the closure walk can
    # see that the code the launcher loads became rewritable.
    fixture = _arrange(tmp_path)
    (fixture.package_root / "lib").chmod(0o777)
    refusal = _refusal(fixture)
    assert refusal.failing_check == "cli_package_closure"
    assert fixture.rows()["cli_package_closure"]["observed"] == "GROUP_OR_OTHER_WRITABLE"


def test_sticky_world_writable_package_root_refused_by_the_closure(
    tmp_path: Path,
) -> None:
    """Sticky is not immutability: entries can still be *added* to the closure.

    ``01777`` is trusted-owned, the tree digest is unchanged, and the ancestor
    walk is satisfied — yet the service/AGENT UID can drop a new module into the
    closure between this gate and Node's own reopen. The closure row is what has
    to see that, because a protected object is defined by what cannot appear in
    it, not only by what cannot be removed.
    """
    fixture = _arrange(tmp_path)
    fixture.package_root.chmod(0o1777)
    assert package_tree_digest(fixture.package_root) == fixture.expected.cli.tree_sha256
    refusal = _refusal(fixture)
    assert refusal.failing_check == "cli_package_closure"
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert fixture.rows()["cli_package_closure"]["observed"] == "GROUP_OR_OTHER_WRITABLE"


def test_sticky_world_writable_closure_subdirectory_refused(tmp_path: Path) -> None:
    """``lib/`` holds the sibling code, so the same rule applies one level in."""
    fixture = _arrange(tmp_path)
    (fixture.package_root / "lib").chmod(0o1777)
    refusal = _refusal(fixture)
    assert refusal.failing_check == "cli_package_closure"
    assert fixture.rows()["cli_package_closure"]["observed"] == "GROUP_OR_OTHER_WRITABLE"


def test_inwindow_sticky_package_root_refused_by_the_closure_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C10: admission is not the only gate — the recheck must fail closed too.

    Every first-pass row passes here; the closure only turns sticky inside the
    spawn window. Relying on admission alone would admit exactly the shape this
    window exists to catch.
    """
    fixture = _arrange(tmp_path)
    _arm(monkeypatch, lambda: fixture.package_root.chmod(0o1777))
    refusal = _refusal(fixture)
    assert refusal.failing_check == "cli_package_closure_recheck"
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    assert fixture.rows()["cli_package_closure"]["passed"] is True


def test_sticky_ambient_ancestor_above_the_stage_stays_admissible(
    tmp_path: Path,
) -> None:
    """The ancestor walk keeps the ``/tmp`` shape the hermetic suite runs under.

    A sticky ancestor *above* the staged artifacts holds neither closure content
    nor a Binding: its sticky bit is what stops a non-owner from renaming or
    removing the trusted-owned entry the walk selects. Refusing it would not make
    the closure safer, and it is the one world-writable shape this gate keeps.
    """
    stage_parent = tmp_path / "ambient"
    stage_parent.mkdir()
    fixture = _arrange(stage_parent)
    stage_parent.chmod(0o1777)  # after staging, so harden_tree cannot clear it
    assert stat.S_IMODE(stage_parent.stat().st_mode) == 0o1777
    result = _attest(fixture)
    os.close(result.exec_fd)
    assert fixture.report()["pass"] is True


def test_cli_interpreter_drift_refused(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    fixture.cli_interpreter.write_bytes(b"#!/bin/false\n# swapped interpreter\n")
    refusal = _refusal(fixture)
    assert refusal.failing_check == "cli_interpreter_sha256"


def test_inwindow_sibling_code_swap_refused_by_the_closure_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C10: the wrapped CLI closure must hold on both sides of the window."""
    fixture = _arrange(tmp_path)
    sibling = fixture.package_root / "lib" / "sibling.js"
    _arm(monkeypatch, lambda: sibling.write_bytes(b"// swapped in the window\n"))
    refusal = _refusal(fixture)
    assert refusal.failing_check == "cli_package_closure_recheck"
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH


# -- direct_acp: one artifact that is both CLI and ACP implementation ---------


def _arrange_direct(tmp_path: Path) -> Fixture:
    fixture = _arrange(tmp_path)
    fixture.expected = SealedRuntimeIdentity(
        launch_kind="direct_acp",
        agent_info_name="OpenCode-shaped",
        protocol_version="1",
        cli=ArtifactClosure(
            kind="native_binary",
            path=str(fixture.cli),
            sha256=_sha256(fixture.cli),
            version="1.0.0",
        ),
    )
    fixture.fixed_env = {}
    fixture.launch = ResolvedLaunchSpec(
        executable=str(fixture.cli),
        argv=(str(fixture.cli), "acp"),
        env_allowlist=("HOME", "PATH"),
        credential_refs=(),
        profile_id="private-direct-1.0",
        profile_revision=1,
        profile_hash="0" * 64,
        config_schema_hash="1" * 64,
    )
    return fixture


def test_direct_acp_boundary_pins_and_exec_s_the_agent_cli_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _spawn_guard(monkeypatch)
    fixture = _arrange_direct(tmp_path)
    result = _attest(fixture)
    try:
        rows = fixture.rows()
        assert fixture.report()["pass"] is True
        # The exec'd descriptor is the sealed AGENT CLI, not an interpreter.
        assert os.fstat(result.exec_fd).st_ino == os.stat(fixture.cli).st_ino
        for required in (
            "cli_pin",
            "cli_sha256",
            "cli_artifact_trust",
            "argv_cli_binding",
            "cli_binding_lost",
        ):
            assert required in rows, required
        # A direct runtime seals no interpreter or adapter entry at all.
        for absent in ("node_pin", "adapter_entry_pin", "env_cli_path_binding"):
            assert absent not in rows, absent
        assert rows["cli_package_closure"]["observed"] == "not declared"
    finally:
        os.close(result.exec_fd)
    assert spawns == []


def test_direct_acp_argv0_must_be_the_sealed_executable(tmp_path: Path) -> None:
    fixture = _arrange_direct(tmp_path)
    other = tmp_path / "stage" / "impostor"
    other.write_bytes(b"# impostor\n")
    import dataclasses as _dc

    refusal = _refusal(
        fixture,
        launch=_dc.replace(fixture.launch, argv=(str(other), "acp")),
    )
    assert refusal.failing_check == "argv_cli_binding"
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH


def test_direct_acp_inwindow_swap_refused_by_the_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _arrange_direct(tmp_path)

    def swap() -> None:
        replacement = tmp_path / "stage" / "swapped-agent"
        replacement.write_bytes(b"# swapped agent cli\n")
        fixture.cli.unlink()
        os.rename(replacement, fixture.cli)

    _arm(monkeypatch, swap)
    refusal = _refusal(fixture)
    assert refusal.failing_check == "cli_binding_lost"
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH


# -- C5/C9/C10: the wrapped source artifacts carry the same trust boundary ---
#
# Node and the adapter entry are source-frozen and hash-pinned, but a digest
# freezes bytes only until someone who can write them changes them — and the
# adapter entry is the one artifact ARS hands to Node *by path*, after the
# attestation descriptor is gone.


def test_writable_node_is_refused(tmp_path: Path) -> None:
    """Node is source-frozen, which freezes bytes, not who may rewrite them."""
    fixture = _arrange(tmp_path)
    fixture.node.chmod(0o666)
    refusal = _refusal(fixture)
    assert refusal.failing_check == "node_artifact_trust"
    assert fixture.rows()["node_artifact_trust"]["observed"] == "GROUP_OR_OTHER_WRITABLE"


def test_writable_adapter_entry_is_refused(tmp_path: Path) -> None:
    fixture = _arrange(tmp_path)
    fixture.entry.chmod(0o666)
    refusal = _refusal(fixture)
    assert refusal.failing_check == "adapter_entry_artifact_trust"
    assert (
        fixture.rows()["adapter_entry_artifact_trust"]["observed"]
        == "GROUP_OR_OTHER_WRITABLE"
    )


def test_writable_adapter_entry_ancestor_is_refused(tmp_path: Path) -> None:
    """The adapter's own ancestor chain is walked, not just the CLI's."""
    fixture = _arrange(tmp_path)
    private = tmp_path / "stage" / "adapter"
    private.mkdir()
    relocated = private / "index.js"
    relocated.write_bytes(fixture.entry.read_bytes())
    bf.harden_tree(private)
    expected = fixture.reseal(
        adapter_entry_path=str(relocated),
        adapter_entry_sha256=_sha256(relocated),
    )
    launch = dataclasses.replace(
        fixture.launch, argv=(str(fixture.node), str(relocated))
    )
    private.chmod(0o777)  # only the adapter's own parent is rewritable

    with pytest.raises(AttestationRefusal) as err:
        _attest(fixture, expected=expected, launch=launch)
    assert err.value.failing_check == "adapter_entry_artifact_trust"
    assert (
        fixture.rows()["adapter_entry_artifact_trust"]["observed"]
        == "GROUP_OR_OTHER_WRITABLE"
    )
    assert err.value.code == RUNTIME_IDENTITY_MISMATCH


def test_inwindow_adapter_entry_rewrite_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An in-place rewrite keeps the inode, so only content can catch it.

    Node opens ``argv[1]`` by path after this gate closes its descriptor, so
    the adapter entry's bytes are re-proven on the far side of the window.
    """
    spawns = _spawn_guard(monkeypatch)
    fixture = _arrange(tmp_path)
    before = os.stat(fixture.entry)

    def rewrite() -> None:
        mode = fixture.entry.stat().st_mode
        with open(fixture.entry, "r+b") as handle:
            handle.write(b"// swapped adapter entry code!!\n")
        fixture.entry.chmod(mode)

    _arm(monkeypatch, rewrite)
    refusal = _refusal(fixture)
    assert refusal.failing_check == "adapter_entry_sha256_recheck"
    assert refusal.code == RUNTIME_IDENTITY_MISMATCH
    # The inode never changed: the existing liveness row could not see this.
    after = os.stat(fixture.entry)
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    assert spawns == []


def test_inwindow_adapter_entry_permission_grant_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Losing the trust property mid-window is itself the refusal."""
    fixture = _arrange(tmp_path)

    def loosen() -> None:
        fixture.entry.chmod(0o666)

    _arm(monkeypatch, loosen)
    refusal = _refusal(fixture)
    assert refusal.failing_check == "adapter_entry_trust_recheck"
    assert (
        fixture.rows()["adapter_entry_trust_recheck"]["observed"]
        == "GROUP_OR_OTHER_WRITABLE"
    )

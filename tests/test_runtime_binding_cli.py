"""PR-B WP5: the ``runtime-binding`` operator surface and provenance inspector.

Exactly four subcommands, no ``--force``, no privilege escalation, no artifact
installation, no daemon restart, no provider contact. Everything here runs over
synthetic Binding roots and fake artifacts under ``tmp_path``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from agent_run_supervisor.cli import _build_parser, main
from agent_run_supervisor.native_acp import runtime_binding as rb
from agent_run_supervisor.native_acp.profile import (
    CLAUDE_AGENT_ACP_0_63_0,
    OPENCODE_NATIVE_ACP,
)

from tests.native_acp import binding_fixtures as bf

_PROFILE_ID = OPENCODE_NATIVE_ACP.profile_id
_OTHER_PROFILE_ID = CLAUDE_AGENT_ACP_0_63_0.profile_id


def _binding_root(tmp_path: Path, *, version: str = "1.18.5", **kwargs) -> Path:
    """A synthetic root whose agent CLI truthfully reports ``version``.

    The fake CLI is a script, so C5 requires its interpreter to be frozen: the
    fixture stages a private shell inside its own hardened tree and names that
    in the shebang, rather than leaning on the host's ``/bin/sh``.
    """
    scratch = tmp_path / "artifacts"
    scratch.mkdir(parents=True, exist_ok=True)
    shell = bf.stage_interpreter(scratch)
    binary = bf.make_native_binary(
        scratch,
        name="agent-cli",
        body="#!" + str(shell) + "\nprintf '%s' '" + version + "'\n",
    )
    slots = {"agent_cli": bf.native_binary_slot(binary, version=version)}
    return bf.build_binding_root(
        tmp_path, OPENCODE_NATIVE_ACP, slots=slots, **kwargs
    )


def _run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    out = capsys.readouterr().out.strip()
    return code, json.loads(out) if out else {}


def _trusted(tmp_path: Path) -> list[str]:
    """Declare this run's own UID as the operator; a fake service UID owns nothing."""
    import os

    return [
        "--trusted-uid",
        str(os.getuid()),
        "--service-uid",
        str(bf.FAKE_SERVICE_UID),
    ]


# -- parser surface ----------------------------------------------------------


def test_parser_exposes_exactly_the_four_subcommands() -> None:
    parser = _build_parser()
    groups = [
        action
        for action in parser._subparsers._group_actions  # type: ignore[union-attr]
        if "runtime-binding" in action.choices
    ]
    assert groups, "runtime-binding is not a registered command"
    binding = groups[0].choices["runtime-binding"]
    sub = [
        action
        for action in binding._subparsers._group_actions  # type: ignore[union-attr]
    ][0]
    assert sorted(sub.choices) == ["inspect-run", "promote", "rollback", "validate"]


def test_no_force_flag_exists_anywhere_in_the_command_group() -> None:
    parser = _build_parser()
    binding = [
        action
        for action in parser._subparsers._group_actions  # type: ignore[union-attr]
        if "runtime-binding" in action.choices
    ][0].choices["runtime-binding"]
    sub = [
        action
        for action in binding._subparsers._group_actions  # type: ignore[union-attr]
    ][0]
    for name, command in sub.choices.items():
        options = {
            option for action in command._actions for option in action.option_strings
        }
        assert not any("force" in option for option in options), name


def test_the_command_path_contains_no_privilege_escalation() -> None:
    """Static assertion: no sudo, su, pkexec, setuid, or shell in the path."""
    src = Path(__file__).resolve().parents[1] / "src" / "agent_run_supervisor"
    banned = ("sudo", "pkexec", "setuid", "seteuid", "shell=True", "os.system")
    for module in ("commands.py", "cli.py", "native_acp/runtime_binding.py"):
        text = (src / module).read_text(encoding="utf-8")
        for needle in banned:
            assert needle not in text, f"{module}: {needle}"
    # And the probe never launches a shell.
    tree = ast.parse((src / "native_acp" / "runtime_binding.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "run":
                for keyword in node.keywords:
                    assert keyword.arg != "shell"


# -- validate ----------------------------------------------------------------


def test_validate_accepts_a_probe_backed_generation(tmp_path: Path, capsys) -> None:
    root = _binding_root(tmp_path)
    code, payload = _run(
        capsys,
        "runtime-binding",
        "validate",
        "--binding-root",
        str(root),
        "--profile",
        _PROFILE_ID,
        "--generation",
        "gen-0001",
        *_trusted(tmp_path),
    )
    assert code == 0
    assert payload["valid"] is True
    assert payload["generation_id"] == "gen-0001"
    assert payload["probe_version"] == "1.18.5"
    assert payload["declared_version"] == "1.18.5"
    assert payload["session_compatibility_epoch"] == 1
    assert payload["adapter_contract_hash"] == (
        OPENCODE_NATIVE_ACP.adapter_contract_hash()
    )


def test_validate_refuses_probe_versus_manifest_mismatch(tmp_path: Path, capsys) -> None:
    """A manifest's version string alone is never proof (C6)."""
    scratch = tmp_path / "artifacts"
    scratch.mkdir(parents=True, exist_ok=True)
    shell = bf.stage_interpreter(scratch)
    binary = bf.make_native_binary(
        scratch,
        name="agent-cli",
        body="#!" + str(shell) + "\nprintf '%s' '9.9.9'\n",
    )
    root = bf.build_binding_root(
        tmp_path,
        OPENCODE_NATIVE_ACP,
        slots={"agent_cli": bf.native_binary_slot(binary, version="1.18.5")},
    )
    code, payload = _run(
        capsys,
        "runtime-binding",
        "validate",
        "--binding-root",
        str(root),
        "--profile",
        _PROFILE_ID,
        "--generation",
        "gen-0001",
        *_trusted(tmp_path),
    )
    assert code == 1
    assert payload["valid"] is False
    assert payload["rule"] == "PROBE_VERSION_MISMATCH"


def test_validate_refuses_an_unknown_profile(tmp_path: Path, capsys) -> None:
    root = _binding_root(tmp_path)
    code, payload = _run(
        capsys,
        "runtime-binding",
        "validate",
        "--binding-root",
        str(root),
        "--profile",
        "opencode-1.18.4",
        "--generation",
        "gen-0001",
        *_trusted(tmp_path),
    )
    assert code == 1
    assert payload["rule"] == "UNKNOWN_PROFILE"


def test_validate_writes_nothing(tmp_path: Path, capsys) -> None:
    root = _binding_root(tmp_path)
    before = sorted(str(path) for path in root.rglob("*"))
    _run(
        capsys,
        "runtime-binding",
        "validate",
        "--binding-root",
        str(root),
        "--profile",
        _PROFILE_ID,
        "--generation",
        "gen-0001",
        *_trusted(tmp_path),
    )
    assert sorted(str(path) for path in root.rglob("*")) == before


# -- promote / rollback -------------------------------------------------------


def test_promote_atomically_replaces_only_the_active_pointer(
    tmp_path: Path, capsys
) -> None:
    root = _binding_root(tmp_path, generation_id="gen-0002", write_pointer=False)
    assert not rb.active_pointer_path(root, _PROFILE_ID).exists()
    code, payload = _run(
        capsys,
        "runtime-binding",
        "promote",
        "--binding-root",
        str(root),
        "--profile",
        _PROFILE_ID,
        "--generation",
        "gen-0002",
        *_trusted(tmp_path),
    )
    assert code == 0
    assert payload["promoted"] is True
    pointer = rb.active_pointer_path(root, _PROFILE_ID)
    assert pointer.is_file() and not pointer.is_symlink()
    assert json.loads(pointer.read_text())["generation_id"] == "gen-0002"
    # Only that one file appeared, and only inside this profile's own subtree.
    assert sorted(path.name for path in root.iterdir()) == [rb.PROFILES_DIRNAME]
    assert sorted(
        path.name for path in rb.profile_binding_dir(root, _PROFILE_ID).iterdir()
    ) == [rb.ACTIVE_FILENAME, rb.GENERATIONS_DIRNAME]


def test_promote_refuses_a_generation_that_cannot_be_validated(
    tmp_path: Path, capsys
) -> None:
    root = _binding_root(tmp_path, generation_id="gen-0003", epoch=0, write_pointer=False)
    code, payload = _run(
        capsys,
        "runtime-binding",
        "promote",
        "--binding-root",
        str(root),
        "--profile",
        _PROFILE_ID,
        "--generation",
        "gen-0003",
        *_trusted(tmp_path),
    )
    assert code == 1
    assert payload["rule"] == "EPOCH_NOT_POSITIVE"
    assert not rb.active_pointer_path(root, _PROFILE_ID).exists()


def test_rollback_refuses_a_generation_that_never_validates(
    tmp_path: Path, capsys
) -> None:
    """``rollback`` re-proves the target with the same validation + probe."""
    root = _binding_root(tmp_path, generation_id="gen-0001")
    bad = rb.generation_manifest_path(root, _PROFILE_ID, "gen-bad").parent
    bad.mkdir(parents=True)
    bf.write_canonical(bad / rb.MANIFEST_FILENAME, {"schema_version": 1})
    bad.chmod(0o755)
    code, payload = _run(
        capsys,
        "runtime-binding",
        "rollback",
        "--binding-root",
        str(root),
        "--profile",
        _PROFILE_ID,
        "--generation",
        "gen-bad",
        *_trusted(tmp_path),
    )
    assert code == 1
    assert payload["valid"] is False
    # The active pointer is untouched.
    pointer = rb.active_pointer_path(root, _PROFILE_ID)
    assert json.loads(pointer.read_text())["generation_id"] == "gen-0001"


def test_rollback_refuses_the_already_active_generation(tmp_path: Path, capsys) -> None:
    root = _binding_root(tmp_path, generation_id="gen-0001")
    code, payload = _run(
        capsys,
        "runtime-binding",
        "rollback",
        "--binding-root",
        str(root),
        "--profile",
        _PROFILE_ID,
        "--generation",
        "gen-0001",
        *_trusted(tmp_path),
    )
    assert code == 1
    assert payload["rule"] == "ALREADY_ACTIVE"


def test_rollback_repromotes_a_validated_generation(tmp_path: Path, capsys) -> None:
    root = _binding_root(tmp_path, generation_id="gen-0001")
    # A second, independently valid generation over the same artifacts.
    manifest = json.loads(
        rb.generation_manifest_path(root, _PROFILE_ID, "gen-0001").read_text()
    )
    older = rb.generation_manifest_path(root, _PROFILE_ID, "gen-0000").parent
    older.mkdir(parents=True)
    manifest["generation_id"] = "gen-0000"
    bf.write_canonical(older / rb.MANIFEST_FILENAME, manifest)
    older.chmod(0o755)

    code, payload = _run(
        capsys,
        "runtime-binding",
        "rollback",
        "--binding-root",
        str(root),
        "--profile",
        _PROFILE_ID,
        "--generation",
        "gen-0000",
        *_trusted(tmp_path),
    )
    assert code == 0
    assert payload["rolled_back_to"] == "gen-0000"
    pointer = rb.active_pointer_path(root, _PROFILE_ID)
    assert json.loads(pointer.read_text())["generation_id"] == "gen-0000"


# -- one root, several independently promoted profiles (C15) ------------------


def _shared_root_with_two_profiles(tmp_path: Path) -> Path:
    """One operator root: OpenCode already promoted, the other profile authored."""
    shared = "shared-binding-root"
    root = _binding_root(tmp_path, generation_id="gen-0001", dirname=shared)
    bf.build_binding_root(
        tmp_path,
        CLAUDE_AGENT_ACP_0_63_0,
        generation_id="gen-0001",
        dirname=shared,
        version="0.63.0",
        reports_version=True,
        write_pointer=False,
    )
    return root


def test_promoting_one_profile_never_disturbs_another_in_the_same_root(
    tmp_path: Path, capsys
) -> None:
    """Sequential operator promotions leave one active selection per profile."""
    root = _shared_root_with_two_profiles(tmp_path)
    promoted = rb.active_pointer_path(root, _PROFILE_ID)
    before = promoted.read_bytes()

    code, payload = _run(
        capsys,
        "runtime-binding",
        "promote",
        "--binding-root",
        str(root),
        "--profile",
        _OTHER_PROFILE_ID,
        "--generation",
        "gen-0001",
        *_trusted(tmp_path),
    )
    assert code == 0
    assert payload["promoted"] is True
    other_pointer = rb.active_pointer_path(root, _OTHER_PROFILE_ID)
    assert json.loads(other_pointer.read_text())["generation_id"] == "gen-0001"
    # The profile nobody promoted is byte-identical and still resolvable.
    assert promoted.read_bytes() == before
    resolved = rb.BindingReader(root, ownership=bf.ownership()).resolve_active(
        OPENCODE_NATIVE_ACP
    )
    assert resolved.contract_identity["profile_id"] == _PROFILE_ID


def test_rollback_is_scoped_to_the_profile_it_names(tmp_path: Path, capsys) -> None:
    root = _shared_root_with_two_profiles(tmp_path)
    # A second, independently valid generation for the *other* profile only.
    manifest_path = rb.generation_manifest_path(root, _OTHER_PROFILE_ID, "gen-0001")
    manifest = json.loads(manifest_path.read_text())
    manifest["generation_id"] = "gen-0000"
    older = rb.generation_manifest_path(root, _OTHER_PROFILE_ID, "gen-0000")
    older.parent.mkdir(parents=True, exist_ok=True)
    bf.write_canonical(older, manifest)
    older.parent.chmod(0o755)

    untouched = rb.active_pointer_path(root, _PROFILE_ID).read_bytes()
    for generation in ("gen-0001", "gen-0000"):
        code, payload = _run(
            capsys,
            "runtime-binding",
            "promote" if generation == "gen-0001" else "rollback",
            "--binding-root",
            str(root),
            "--profile",
            _OTHER_PROFILE_ID,
            "--generation",
            generation,
            *_trusted(tmp_path),
        )
        assert code == 0, payload
    other_pointer = rb.active_pointer_path(root, _OTHER_PROFILE_ID)
    assert json.loads(other_pointer.read_text())["generation_id"] == "gen-0000"
    assert rb.active_pointer_path(root, _PROFILE_ID).read_bytes() == untouched


# -- inspect-run (C13) --------------------------------------------------------


def _sealed_run_dir(tmp_path: Path) -> Path:
    """A Run directory with the artifacts admission would have written."""
    from agent_run_supervisor.native_acp.spec import (
        AgentRunRequest,
        InputRef,
        RunLimits,
        RunSpecAssembler,
        spec_hash,
    )

    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    request = AgentRunRequest(
        owner="hermes",
        namespace="hermes/ns",
        profile_id=_PROFILE_ID,
        session_reuse="none",
        ars_session_id=None,
        expected_binding_hash=None,
        input_refs=(InputRef(ref="prompt:inline", content_hash="sha256:" + "a" * 64),),
        requested_model="kimi-for-coding/k3",
        requested_effort="max",
        grant_ref="grant:1",
        grant_hash="sha256:" + "b" * 64,
        grant_role_hash="sha256:" + "c" * 64,
        grant_capabilities=("read",),
        mcp_snapshot_hashes=(),
        credential_refs=("kimi-for-coding",),
        limits=RunLimits(),
        evidence_policy_hash="sha256:" + "d" * 64,
        recovery_policy_hash="sha256:" + "e" * 64,
    )
    runtime = bf.admitted(tmp_path / "inspect-binding", OPENCODE_NATIVE_ACP)
    assembler = RunSpecAssembler(request)
    assembler.resolve_profile(
        __import__(
            "agent_run_supervisor.native_acp.profile", fromlist=["DEFAULT_REGISTRY"]
        ).DEFAULT_REGISTRY
    )
    assembler.bind_workspace(root=workspace)
    launch = assembler.resolve_launch(runtime=runtime)
    spec = assembler.seal(run_id="run-inspect-1", submitted_at="2026-07-26T00:00:00+00:00")

    run_dir = tmp_path / "native-runs" / "run-inspect-1"
    run_dir.mkdir(parents=True)
    spec_payload = spec.to_dict()
    spec_payload["spec_hash"] = spec_hash(spec)
    (run_dir / "spec.json").write_text(json.dumps(spec_payload, indent=2, sort_keys=True))
    launch_payload = launch.to_dict()
    launch_payload["launch_spec_hash"] = launch.launch_hash()
    (run_dir / "launch.json").write_text(
        json.dumps(launch_payload, indent=2, sort_keys=True)
    )
    return run_dir


def test_inspect_run_recomputes_the_seal_and_reports_provenance(
    tmp_path: Path, capsys
) -> None:
    run_dir = _sealed_run_dir(tmp_path)
    code, payload = _run(capsys, "runtime-binding", "inspect-run", "--run-dir", str(run_dir))
    assert code == 0
    assert payload["seal_verified"] is True
    assert payload["legacy_launch_record"] is False
    assert payload["matches_spec"] is True
    assert payload["profile_id"] == _PROFILE_ID
    assert payload["adapter_contract_hash"] == (
        OPENCODE_NATIVE_ACP.adapter_contract_hash()
    )
    assert payload["launch_kind"] == "direct_acp"
    assert payload["binding"]["generation_id"] == "gen-0001"
    assert payload["binding"]["slot_set_hash"]
    assert payload["binding"]["slot_hashes"]
    assert payload["cli"]["version"] == "1.0.0"
    assert payload["cli"]["sha256"]
    assert payload["session_compatibility_epoch"] == 1


@pytest.mark.parametrize(
    "field", ["executable", "argv", "profile_hash", "expected_runtime"]
)
def test_inspect_run_detects_a_single_mutated_field(
    tmp_path: Path, capsys, field: str
) -> None:
    run_dir = _sealed_run_dir(tmp_path)
    payload = json.loads((run_dir / "launch.json").read_text())
    payload[field] = "tampered" if isinstance(payload[field], str) else {"x": 1}
    (run_dir / "launch.json").write_text(json.dumps(payload, indent=2, sort_keys=True))
    code, report = _run(
        capsys, "runtime-binding", "inspect-run", "--run-dir", str(run_dir)
    )
    assert code == 1
    assert report["seal_verified"] is False


def test_inspect_run_degrades_gracefully_on_a_legacy_launch_record(
    tmp_path: Path, capsys
) -> None:
    """A pre-PR-B record has no provenance block; it is reported, not refused.

    Graceful is not unconditional: the missing embedded seal means the record
    is verified against ``spec.json``, and that verification still has to pass.
    """
    run_dir = _legacy_run_dir(tmp_path)
    code, report = _run(
        capsys, "runtime-binding", "inspect-run", "--run-dir", str(run_dir)
    )
    assert code == 0
    assert report["legacy_launch_record"] is True
    assert report["binding"] is None
    assert report["embedded_seal"] is None
    assert report["matches_spec"] is True
    assert report["profile_id"] == _PROFILE_ID


def test_inspect_run_reports_a_missing_run_directory_without_writing(
    tmp_path: Path, capsys
) -> None:
    missing = tmp_path / "absent-run"
    code, report = _run(
        capsys, "runtime-binding", "inspect-run", "--run-dir", str(missing)
    )
    assert code == 1
    assert report["error"] == "LAUNCH_RECORD_MISSING"
    assert not missing.exists()


# -- C7: the operator surface obeys the Binding-root ancestor policy ----------


def test_promote_refuses_a_root_reached_through_a_symlinked_ancestor(
    tmp_path: Path, capsys
) -> None:
    """A redirected ancestor must not become a promotion target."""
    actual = tmp_path / "actual"
    actual.mkdir()
    root = _binding_root(actual, write_pointer=False)
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)

    code, report = _run(
        capsys,
        "runtime-binding",
        "promote",
        "--binding-root",
        str(alias / "binding-root"),
        "--profile",
        _PROFILE_ID,
        "--generation",
        "gen-0001",
        *_trusted(tmp_path),
    )
    assert code == 1
    assert report["rule"] == "SYMLINKED_ANCESTOR"
    assert not rb.active_pointer_path(root, _PROFILE_ID).exists()


# -- C6: promotion activates exactly the generation the probe proved ---------


def _retarget_seam(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, replacement: bytes
) -> None:
    """Land a concurrent generation replacement while the probe is running."""
    calls: list[int] = []
    original = rb.probe_slot_version

    def racing(resolved, profile):
        result = original(resolved, profile)
        calls.append(1)
        if len(calls) == 1:
            manifest.write_bytes(replacement)
        return result

    monkeypatch.setattr(rb, "probe_slot_version", racing)


def _second_manifest(tmp_path: Path, root: Path, *, version: str) -> bytes:
    """A statically valid manifest for the same generation, naming a new CLI."""
    scratch = tmp_path / "swapped"
    scratch.mkdir(parents=True, exist_ok=True)
    shell = bf.stage_interpreter(scratch)
    binary = bf.make_native_binary(
        scratch,
        name="agent-cli",
        body="#!" + str(shell) + "\nprintf '%s' '" + version + "'\n",
    )
    manifest = json.loads(
        rb.generation_manifest_path(root, _PROFILE_ID, "gen-0001").read_text()
    )
    manifest["slots"] = {"agent_cli": bf.native_binary_slot(binary, version=version)}
    return bf.canonical(manifest).encode("utf-8")


def test_promote_never_activates_a_generation_the_probe_did_not_see(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _binding_root(tmp_path, write_pointer=False)
    manifest = rb.generation_manifest_path(root, _PROFILE_ID, "gen-0001")
    _retarget_seam(
        monkeypatch, manifest, _second_manifest(tmp_path, root, version="9.9.9")
    )

    code, report = _run(
        capsys,
        "runtime-binding",
        "promote",
        "--binding-root",
        str(root),
        "--profile",
        _PROFILE_ID,
        "--generation",
        "gen-0001",
        *_trusted(tmp_path),
    )
    assert code == 1
    assert report["rule"] == "GENERATION_CHANGED"
    assert not rb.active_pointer_path(root, _PROFILE_ID).exists()


def test_rollback_never_activates_a_generation_the_probe_did_not_see(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _binding_root(tmp_path, write_pointer=False)
    other = _binding_root(
        tmp_path / "other", generation_id="gen-0002", write_pointer=False
    )
    # Make gen-0002 the active pointer so gen-0001 is a genuine rollback target.
    rb.generation_manifest_path(root, _PROFILE_ID, "gen-0002").parent.mkdir(
        parents=True, exist_ok=True
    )
    source = rb.generation_manifest_path(other, _PROFILE_ID, "gen-0002")
    target = rb.generation_manifest_path(root, _PROFILE_ID, "gen-0002")
    target.write_bytes(source.read_bytes())
    bf.write_canonical(
        rb.active_pointer_path(root, _PROFILE_ID),
        {
            "schema_version": rb.BINDING_SCHEMA_VERSION,
            "profile_id": _PROFILE_ID,
            "generation_id": "gen-0002",
            "manifest_sha256": bf.sha256_file(target),
        },
    )
    before = rb.active_pointer_path(root, _PROFILE_ID).read_bytes()

    manifest = rb.generation_manifest_path(root, _PROFILE_ID, "gen-0001")
    _retarget_seam(
        monkeypatch, manifest, _second_manifest(tmp_path, root, version="9.9.9")
    )
    code, report = _run(
        capsys,
        "runtime-binding",
        "rollback",
        "--binding-root",
        str(root),
        "--profile",
        _PROFILE_ID,
        "--generation",
        "gen-0001",
        *_trusted(tmp_path),
    )
    assert code == 1
    assert report["rule"] == "GENERATION_CHANGED"
    assert rb.active_pointer_path(root, _PROFILE_ID).read_bytes() == before


# -- C13: a legacy launch record is readable, not exempt from verification ---


def _legacy_run_dir(tmp_path: Path) -> Path:
    """A faithful pre-PR-B Run: no provenance, and a spec sealed over it."""
    run_dir = _sealed_run_dir(tmp_path)
    payload = json.loads((run_dir / "launch.json").read_text())
    payload.pop("runtime_provenance")
    payload.pop("expected_runtime")
    payload.pop("launch_spec_hash")
    (run_dir / "launch.json").write_text(json.dumps(payload, indent=2, sort_keys=True))

    from agent_run_supervisor.commands import _recompute_launch_hash

    spec = json.loads((run_dir / "spec.json").read_text())
    spec["launch_spec_hash"] = _recompute_launch_hash(payload)
    (run_dir / "spec.json").write_text(json.dumps(spec, indent=2, sort_keys=True))
    return run_dir


def test_inspect_run_refuses_a_mutated_legacy_record(tmp_path: Path, capsys) -> None:
    """Forged legacy evidence must never be reported as a success."""
    run_dir = _legacy_run_dir(tmp_path)
    payload = json.loads((run_dir / "launch.json").read_text())
    payload["executable"] = "/tmp/attacker-cli"
    (run_dir / "launch.json").write_text(json.dumps(payload, indent=2, sort_keys=True))

    code, report = _run(
        capsys, "runtime-binding", "inspect-run", "--run-dir", str(run_dir)
    )
    assert code == 1
    assert report["legacy_launch_record"] is True
    assert report["matches_spec"] is False


def test_inspect_run_refuses_a_legacy_record_with_no_spec_to_verify(
    tmp_path: Path, capsys
) -> None:
    run_dir = _legacy_run_dir(tmp_path)
    (run_dir / "spec.json").unlink()

    code, report = _run(
        capsys, "runtime-binding", "inspect-run", "--run-dir", str(run_dir)
    )
    assert code == 1
    assert report["legacy_launch_record"] is True
    assert report["spec_launch_spec_hash"] is None
    assert report["matches_spec"] is False

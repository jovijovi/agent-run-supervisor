"""PR-B WP3: the single per-Run Binding read, revalidation, and sealing (C8).

Hermetic throughout: synthetic Binding roots and fake artifacts under
``tmp_path``, no daemon socket, no real CLI, no credential, no provider.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from agent_run_supervisor.arsd import admission, protocol
from agent_run_supervisor.native_acp import runtime_binding as rb
from agent_run_supervisor.native_acp.profile import AgentProfile, ProfileRegistry
from agent_run_supervisor.native_acp.spec import AgentRunRequest

from tests.arsd.test_admission import (
    bindingless_contract,
    codex_admission_profile,
    codex_binding_root,
    codex_wire_request,
    submit_payload,
    valid_wire_request,
)
from tests.native_acp import binding_fixtures as bf

_SRC = Path(__file__).resolve().parents[2] / "src" / "agent_run_supervisor"


def _binding_profile(tmp_path: Path) -> AgentProfile:
    """A Codex-shaped profile whose contract declares Binding slots."""
    return codex_admission_profile(tmp_path)


def test_admission_reads_the_pointer_once_and_one_generation_once(
    tmp_path: Path,
) -> None:
    profile = _binding_profile(tmp_path)
    root = codex_binding_root(tmp_path, profile)
    rb.reset_read_counters()
    admitted = admission.resolve_runtime_binding(
        profile, binding_root=root, ownership=bf.ownership()
    )
    assert rb.read_counters() == {"registration": 0, "active": 1, "generation": 1}
    assert admitted is not None
    assert admitted.resolved.generation_id == "gen-0001"


def test_one_configured_root_admits_every_registered_profile(tmp_path: Path) -> None:
    """C15: one ``--binding-root`` serves the whole closed registry at once."""
    from agent_run_supervisor.native_acp.profile import DEFAULT_REGISTRY

    root = tmp_path / "shared-binding-root"
    profile_scoped = [
        pid
        for pid in DEFAULT_REGISTRY.ids()
        if not DEFAULT_REGISTRY.get(pid).contract.requires_agent_registration
    ]
    for profile_id in profile_scoped:
        root = bf.build_binding_root(
            tmp_path, DEFAULT_REGISTRY.get(profile_id), dirname="shared-binding-root"
        )
    for profile_id in profile_scoped:
        rb.reset_read_counters()
        admitted = admission.resolve_runtime_binding(
            DEFAULT_REGISTRY.get(profile_id),
            binding_root=root,
            ownership=bf.ownership(),
        )
        assert admitted is not None
        assert admitted.resolved.contract_identity["profile_id"] == profile_id
        # Still one pointer and one generation, per Run, per profile.
        assert rb.read_counters() == {"registration": 0, "active": 1, "generation": 1}


def test_admission_reads_only_the_resolved_profiles_own_subtree(
    tmp_path: Path,
) -> None:
    """R1/C15: the subtree comes from the resolved closed profile, nothing else."""
    profile = _binding_profile(tmp_path)
    root = codex_binding_root(tmp_path, profile)
    subtree = rb.profile_binding_dir(root, profile.profile_id)
    subtree.rename(subtree.with_name("some-other-profile"))
    with pytest.raises(rb.BindingRefusal) as err:
        admission.resolve_runtime_binding(
            profile, binding_root=root, ownership=bf.ownership()
        )
    assert err.value.rule == "PROFILE_BINDING_ABSENT"


def test_only_the_binding_module_opens_a_binding_root() -> None:
    """C8 is structural, not advisory: the import graph is the proof."""
    allowed = {
        _SRC / "native_acp" / "runtime_binding.py",  # the only opener
        _SRC / "arsd" / "admission.py",              # the only Run-path caller
        _SRC / "commands.py",                        # the operator surface
        _SRC / "arsd" / "handlers.py",               # per-Run wiring only
        _SRC / "native_acp" / "attestation.py",      # trust helpers + refusal type
        _SRC / "native_acp" / "spec.py",             # consumes the resolved value
        _SRC / "native_acp" / "run_task.py",         # carries the resolved value
    }
    def _imports_module(names: list[str]) -> bool:
        # Exact module segments only: ``cmd_runtime_binding`` is a command
        # entry point, not an opener of a Binding root.
        return any(
            "runtime_binding" in name.split(".") for name in names if isinstance(name, str)
        )

    importers: list[str] = []
    for path in _SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            if _imports_module(names) and path not in allowed:
                importers.append(str(path))
    assert importers == []
    # Reconciliation and the socket server do not even name it.
    for module in ("reconcile.py", "server.py"):
        assert "runtime_binding" not in (
            (_SRC / "arsd" / module).read_text(encoding="utf-8")
        ), module


def test_no_binding_read_happens_after_admission_during_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Run is sealed at admission: spawn and finalization re-read nothing."""
    pytest.importorskip("acp")
    import sys

    from agent_run_supervisor.native_acp import profile as profile_module
    from agent_run_supervisor.native_acp.run_task import RunTask

    monkeypatch.setitem(
        profile_module._REGISTERED_EXECUTABLES,
        "codex-acp-admission",
        Path(sys.executable),
    )
    profile = _binding_profile(tmp_path)
    root = codex_binding_root(tmp_path, profile)
    admitted = admission.resolve_runtime_binding(
        profile, binding_root=root, ownership=bf.ownership()
    )
    workspace = tmp_path / "ws-readonce"
    workspace.mkdir()
    request = protocol.parse_submit(
        submit_payload(request=codex_wire_request(), workspace_root=str(workspace))
    ).request
    task = RunTask(
        request=request,
        prompt_text="hello",
        run_id="run-readonce-1",
        workspace_root=workspace,
        registry=ProfileRegistry((profile,)),
        supervisor_root=tmp_path / "svroot-readonce",
        submitted_at="2026-07-26T00:00:00+00:00",
        runtime_binding=admitted,
    )
    # Counters reset AFTER admission: everything the Run does from here on adds
    # zero reads, whatever it succeeds or fails on.
    rb.reset_read_counters()
    asyncio.run(task.run())
    assert rb.read_counters() == {"registration": 0, "active": 0, "generation": 0}


def test_admission_revalidates_the_artifact_digest_against_the_trusted_path(
    tmp_path: Path,
) -> None:
    profile = _binding_profile(tmp_path)
    root = codex_binding_root(tmp_path, profile)
    manifest = json.loads(
        rb.generation_manifest_path(root, profile.profile_id, "gen-0001").read_text()
    )
    launcher = Path(manifest["slots"]["downstream_cli"]["launcher_path"])
    mode = launcher.stat().st_mode
    launcher.write_text("#!/bin/sh\n# swapped after promotion\n", encoding="utf-8")
    launcher.chmod(mode)
    with pytest.raises(rb.BindingRefusal) as err:
        admission.resolve_runtime_binding(
            profile, binding_root=root, ownership=bf.ownership()
        )
    assert err.value.rule in ("ARTIFACT_DIGEST_MISMATCH", "PACKAGE_TREE_DIGEST_MISMATCH")


def test_a_contract_revision_bump_fails_the_promoted_generation_closed(
    tmp_path: Path,
) -> None:
    profile = _binding_profile(tmp_path)
    root = codex_binding_root(tmp_path, profile)
    assert admission.resolve_runtime_binding(
        profile, binding_root=root, ownership=bf.ownership()
    )
    revised = dataclasses.replace(profile, revision=profile.revision + 1)
    with pytest.raises(rb.BindingRefusal) as err:
        admission.resolve_runtime_binding(
            revised, binding_root=root, ownership=bf.ownership()
        )
    assert err.value.rule == "CONTRACT_IDENTITY_MISMATCH"


def test_a_profile_requiring_a_binding_refuses_when_no_root_is_configured(
    tmp_path: Path,
) -> None:
    with pytest.raises(rb.BindingRefusal) as err:
        admission.resolve_runtime_binding(_binding_profile(tmp_path), binding_root=None)
    assert err.value.rule == "BINDING_ROOT_NOT_CONFIGURED"


def test_a_bindingless_profile_needs_no_root_and_reads_nothing() -> None:
    profile = AgentProfile(
        profile_id="bindingless-1.0",
        revision=1,
        executable_key="python-fake",
        argv_template=("agent.py",),
        env_allowlist=("PATH",),
        credential_slots=(),
        model_selector_id="model",
        effort_selector_id="effort",
        default_model="a/b",
        default_effort="max",
        registered_models=("a/b",),
        allowed_efforts=("max",),
        requires_session_load=False,
        config_schema={"selectors": {}},
        contract=bindingless_contract(),
    )
    rb.reset_read_counters()
    assert admission.resolve_runtime_binding(profile, binding_root=None) is None
    assert rb.read_counters() == {"registration": 0, "active": 0, "generation": 0}


def test_a_pre_pr_b_run_directory_stays_readable(tmp_path: Path) -> None:
    """C12: old Runs stay readable — none of the readers gained a new field."""
    from agent_run_supervisor.native_acp import storage as native_storage

    run_dir = tmp_path / "native-runs" / "run-legacy-1"
    run_dir.mkdir(parents=True)
    # Exactly the pre-PR-B artifact shape: no runtime provenance, no sealed
    # runtime identity, no epoch anywhere.
    (run_dir / "launch.json").write_text(
        json.dumps(
            {
                "executable": "/opt/legacy/opencode",
                "argv": ["/opt/legacy/opencode", "acp"],
                "env_allowlist": ["HOME", "PATH"],
                "credential_refs": [],
                "profile_id": "opencode-1.18.4",
                "profile_revision": 2,
                "profile_hash": "0" * 64,
                "config_schema_hash": "1" * 64,
                "permission_env": [],
                "transport": "stdio",
                "launch_spec_hash": "2" * 64,
            },
            sort_keys=True,
            indent=2,
        )
    )
    # Built through the unchanged result builder, so this is byte-for-byte the
    # terminal a pre-PR-B Run wrote — not a hand-shaped approximation of one.
    from agent_run_supervisor.exit_classifier import AgentRunStatus
    from agent_run_supervisor.result import build_result_payload

    native_storage.write_once_json(
        run_dir / "result.json",
        build_result_payload(
            run_id="run-legacy-1",
            status=AgentRunStatus.COMPLETED,
            origin="acp",
            detail_code=None,
            retryable=False,
            exit_code=0,
            signal=None,
            stop_reason="end_turn",
            usage=None,
            final_message="ok",
            truncated=False,
            truncate_reason=None,
            run_dir=run_dir,
        ),
    )
    state = admission.inspect_terminal_result(run_dir, run_id="run-legacy-1")
    assert state.kind is native_storage.NativeTerminalKind.TRUSTED
    assert admission.read_result(run_dir)["status"] == "completed"
    assert admission.has_terminal_result(run_dir) is True
    assert json.loads((run_dir / "launch.json").read_text())["profile_id"] == (
        "opencode-1.18.4"
    )


def test_no_caller_facing_runtime_selection_field_exists(tmp_path: Path) -> None:
    """C12/R1: admission never grew a runtime-selection surface to filter."""
    fields = {field.name for field in dataclasses.fields(AgentRunRequest)}
    for forbidden in admission.FORBIDDEN_RUNTIME_SELECTION_FIELDS:
        assert forbidden not in fields, forbidden
        payload = submit_payload(
            request={**valid_wire_request(), forbidden: "anything"},
            workspace_root=str(tmp_path),
        )
        with pytest.raises(protocol.ProtocolError) as err:
            protocol.parse_submit(payload)
        assert err.value.code == protocol.INVALID_REQUEST


# -- the agent gate and the agent-anchored read ------------------------------


def _standard():
    from agent_run_supervisor.native_acp.profile import STANDARD_NATIVE_ACP_V1

    return STANDARD_NATIVE_ACP_V1


def test_an_agent_scoped_run_reads_one_registration_one_pointer_one_generation(
    tmp_path: Path,
) -> None:
    profile = _standard()
    root = bf.build_agent_binding_root(tmp_path, profile, bf.FAKE_ALPHA_ID)
    rb.reset_read_counters()
    admitted = admission.resolve_runtime_binding(
        profile,
        binding_root=root,
        ownership=bf.ownership(),
        agent_id=bf.FAKE_ALPHA_ID,
    )
    assert rb.read_counters() == {"registration": 1, "active": 1, "generation": 1}
    assert admitted is not None
    assert admitted.registration is not None
    assert admitted.registration.agent_id == bf.FAKE_ALPHA_ID


def test_a_legacy_run_reads_no_registration(tmp_path: Path) -> None:
    profile = _binding_profile(tmp_path)
    root = codex_binding_root(tmp_path, profile)
    rb.reset_read_counters()
    admission.resolve_runtime_binding(
        profile, binding_root=root, ownership=bf.ownership()
    )
    assert rb.read_counters() == {"registration": 0, "active": 1, "generation": 1}


def test_the_agent_gate_refuses_a_missing_agent_before_reading(tmp_path: Path) -> None:
    profile = _standard()
    root = bf.build_agent_binding_root(tmp_path, profile, bf.FAKE_ALPHA_ID)
    rb.reset_read_counters()
    with pytest.raises(rb.BindingRefusal) as excinfo:
        admission.resolve_runtime_binding(
            profile, binding_root=root, ownership=bf.ownership()
        )
    assert excinfo.value.rule == "AGENT_SCOPE_REQUIRED"
    assert rb.read_counters() == {"registration": 0, "active": 0, "generation": 0}


def test_the_agent_gate_refuses_an_agent_on_a_legacy_profile(tmp_path: Path) -> None:
    profile = _binding_profile(tmp_path)
    root = codex_binding_root(tmp_path, profile)
    rb.reset_read_counters()
    with pytest.raises(rb.BindingRefusal) as excinfo:
        admission.resolve_runtime_binding(
            profile,
            binding_root=root,
            ownership=bf.ownership(),
            agent_id=bf.FAKE_ALPHA_ID,
        )
    assert excinfo.value.rule == "AGENT_SCOPE_FORBIDDEN"
    assert rb.read_counters() == {"registration": 0, "active": 0, "generation": 0}


def test_g1_2_an_unmodified_pre_merge_root_still_resolves_every_live_profile(
    tmp_path: Path,
) -> None:
    """The three live subtrees are byte-identical to today's layout."""
    from agent_run_supervisor.native_acp.profile import DEFAULT_REGISTRY

    root = tmp_path / "pre-merge-root"
    live = [
        DEFAULT_REGISTRY.get(pid)
        for pid in DEFAULT_REGISTRY.ids()
        if not DEFAULT_REGISTRY.get(pid).contract.requires_agent_registration
    ]
    for profile in live:
        root = bf.build_binding_root(tmp_path, profile, dirname="pre-merge-root")
    for profile in live:
        resolved = admission.resolve_runtime_binding(
            profile, binding_root=root, ownership=bf.ownership()
        )
        assert resolved is not None
        assert set(resolved.resolved.contract_identity) == {
            "profile_id",
            "profile_revision",
            "adapter_contract_hash",
        }
        pointer = json.loads(
            rb.active_pointer_path(root, profile.profile_id).read_text(encoding="utf-8")
        )
        assert set(pointer) == {
            "schema_version",
            "profile_id",
            "generation_id",
            "manifest_sha256",
        }


def test_g1_2_field_sets_widen_only_for_an_agent_scoped_contract(
    tmp_path: Path,
) -> None:
    profile = _standard()
    root = bf.build_agent_binding_root(tmp_path, profile, bf.FAKE_ALPHA_ID)
    admitted = admission.resolve_runtime_binding(
        profile,
        binding_root=root,
        ownership=bf.ownership(),
        agent_id=bf.FAKE_ALPHA_ID,
    )
    assert set(admitted.resolved.contract_identity) == {
        "profile_id",
        "profile_revision",
        "adapter_contract_hash",
        "agent_id",
        "agent_registration_hash",
    }

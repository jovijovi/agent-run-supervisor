"""A3 / A4 / A12 — the boundaries the reset exists to establish, proven structurally.

**A3, the filesystem boundary.** ARS-owned *writable* surfaces are exactly two:
the supervisor root through the single storage seam, and the configured UDS
runtime path. Nothing else. Read-only access is permitted wherever the paths
live, including below ``$HOME`` and through symlinks and PATH shims. ARS never
creates, writes, populates, stages, mirrors, repairs, or deletes AGENT auth,
configuration, cache, plugin, or Session state, and never *inspects* those
surfaces as a control surface.

**A4, no deployment facts in source.** No file under ``src/`` contains an
absolute path to, a digest of, or a version of any external agent, adapter, or
interpreter. Those are operator facts now, carried by one registry entry.

**A12, no endpoint seam.** v1 is stdio by definition: one ARS-owned local
``ManagedProcess`` per Run from spawn to reap, and no key, field, branch, or
dependency anticipating a remote transport or an attach. ``transport`` is
refused as an unknown registry key rather than modelled as a one-valued one.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "agent_run_supervisor"
NATIVE = SRC / "native_acp"
ARSD = SRC / "arsd"

# The reset line only. The acpx-era modules are out of scope for this reset and
# are removed by their own separately authorized decision.
RESET_SCOPE = (
    *sorted(NATIVE.glob("*.py")),
    *sorted(ARSD.glob("*.py")),
    SRC / "managed_process.py",
    SRC / "session.py",
    SRC / "redaction.py",
    SRC / "result.py",
    SRC / "event_store.py",
    SRC / "cli.py",
    SRC / "commands.py",
)


def reset_sources() -> list[tuple[Path, str]]:
    return [(path, path.read_text(encoding="utf-8")) for path in RESET_SCOPE]


# -- D2: the retired modules are gone ----------------------------------------


@pytest.mark.parametrize("name", ["runtime_binding.py", "attestation.py"])
def test_the_retired_binding_modules_are_absent_from_the_tree(name):
    assert not (NATIVE / name).exists(), f"{name} still exists"


@pytest.mark.parametrize("name", ["runtime_binding", "attestation"])
def test_no_module_imports_the_retired_layer(name):
    for path, text in reset_sources():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert name not in node.module, f"{path.name} imports {name}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert name not in alias.name, f"{path.name} imports {name}"


def test_no_module_recreates_the_retired_concepts_under_another_name():
    """D2: not renamed, not relocated, not re-expressed — removed.

    Two survivals are deliberate and are asserted for separately below: the
    retired identity *field names* on ``SessionRecord``, which keep a pre-reset
    record readable, and the retired wire *field names* in admission's forbidden
    set, which are there precisely to prove they are not request fields. Both
    are name-only, carry no value, and gate nothing.
    """
    banned = (
        "ArtifactClosure",
        "SealedRuntimeIdentity",
        "RuntimeProvenance",
        "BindingReader",
        "AdmittedRuntimeBinding",
        "TrustedOwnership",
        "attest_spawn_boundary",
        "generation_hash",
        "slot_set_hash",
        "manifest_sha256",
        "acceptance_receipt",
        "tree_sha256",
    )
    for path, text in reset_sources():
        for name in banned:
            assert name not in text, f"{path.name} still carries {name!r}"


def test_the_retired_identity_names_survive_only_as_names():
    """A retired concept may be *named* to refuse or to read it, never to use it."""
    from agent_run_supervisor.arsd import admission
    from agent_run_supervisor.session import LEGACY_SESSION_IDENTITY_FIELDS

    # Named to be refused: a request field that must not exist.
    assert "adapter_contract_hash" in admission.FORBIDDEN_RUNTIME_SELECTION_FIELDS
    # Named to be read: a pre-reset record stays status/list/close-readable.
    assert "native_adapter_contract_hash" in LEGACY_SESSION_IDENTITY_FIELDS
    # And nowhere else in the reset line.
    allowed = {"admission.py", "session.py"}
    for path, text in reset_sources():
        if path.name in allowed:
            continue
        assert "adapter_contract_hash" not in text, f"{path.name} uses a retired hash"


def test_no_credential_root_or_ownership_gate_survives():
    banned = (
        "credential_root",
        "trusted_uid",
        "service_uid",
        "required_absence",
        "mode_enforcement",
        "st_uid ==",
    )
    for path, text in reset_sources():
        for name in banned:
            assert name not in text, f"{path.name} still carries {name!r}"


# -- A4: no deployment facts in source ---------------------------------------

_SHA256_RE = re.compile(r"\b[0-9a-f]{64}\b")
_ARTIFACT_PATH_RE = re.compile(r"[\"']/(opt|usr|home)/[^\"'\n]*[\"']")


def test_no_source_file_carries_an_artifact_digest():
    for path, text in reset_sources():
        # Test-only hex fixtures never live in src/; a 64-hex literal here is a
        # frozen artifact identity by construction.
        assert not _SHA256_RE.search(text), f"{path.name} carries a digest literal"


def test_no_source_file_carries_an_absolute_external_agent_path():
    for path, text in reset_sources():
        found = _ARTIFACT_PATH_RE.findall(text)
        assert not found, f"{path.name} carries an absolute deployment path"


def test_no_source_file_names_an_external_agent_adapter_or_interpreter_version():
    banned = (
        "ARTIFACT_MATERIALIZATION_PREFIX",
        "agent-run-supervisor/artifacts",
        "node_modules",
        "@agentclientprotocol",
        "--no-global-search-paths",
        "CODEX_PATH",
        "CODEX_HOME",
        "CLAUDE_CODE_EXECUTABLE",
        "OPENCODE_PERMISSION" "_VERSION",
    )
    for path, text in reset_sources():
        for name in banned:
            assert name not in text, f"{path.name} names {name!r}"


def test_the_one_mediation_pair_is_the_only_agent_shaped_literal():
    """The mediation binding is keyed by the capability family it mediates.

    Its key is an agent-defined environment name, which is unavoidable — the
    knob belongs to that agent — but it is the *only* such literal, it carries
    no path, digest, or version, and it lives in the mediation table rather than
    in a per-agent profile.
    """
    from agent_run_supervisor.native_acp import profile as profile_mod

    keys = {key for pairs in profile_mod.MEDIATION_BINDINGS.values() for key, _ in pairs}
    assert keys == {"OPENCODE_PERMISSION"}
    source = (NATIVE / "profile.py").read_text(encoding="utf-8")
    assert source.count("OPENCODE_PERMISSION") == 1


# -- A12: no endpoint / transport / attach seam ------------------------------


@pytest.mark.parametrize(
    "token",
    ["endpoint", "attach_to", "websocket", "grpc", "sse", "listen_addr", "bind_host"],
)
def test_no_remote_transport_token_appears_in_the_reset_line(token):
    """Word-boundary matched: ``dataclasses`` is not an SSE seam.

    ``admission.py`` is excluded because the only place it names these tokens is
    the forbidden-request-field tuple, whose entire purpose is to assert that
    they are *not* request fields. That list is checked in its own right below.
    """
    pattern = re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)
    for path, text in reset_sources():
        if path.name == "admission.py":
            continue
        assert not pattern.search(text), f"{path.name} names {token!r}"


def test_the_forbidden_field_list_is_the_only_place_a_transport_token_appears():
    from agent_run_supervisor.arsd import admission

    forbidden = set(admission.FORBIDDEN_RUNTIME_SELECTION_FIELDS)
    assert {"transport", "endpoint", "attach", "remote"} <= forbidden
    text = (ARSD / "admission.py").read_text(encoding="utf-8")
    for token in ("endpoint", "attach", "remote", "transport"):
        # Once in the tuple, and nowhere else in the module.
        assert text.count(f'"{token}"') == 1


def test_transport_is_not_a_field_anywhere_in_the_reset_line():
    """Refused as an unknown registry key, and absent from every dataclass."""
    import dataclasses

    from agent_run_supervisor.native_acp import spec
    from agent_run_supervisor.native_acp.agent_registration import ENTRY_FIELDS

    assert "transport" not in ENTRY_FIELDS
    for name in dir(spec):
        candidate = getattr(spec, name)
        if dataclasses.is_dataclass(candidate) and isinstance(candidate, type):
            fields = {field.name for field in dataclasses.fields(candidate)}
            assert "transport" not in fields, f"spec.{name} carries a transport field"


def test_transport_is_refused_as_an_unknown_registry_key(tmp_path):
    from agent_run_supervisor.native_acp import agent_registry

    from tests.native_acp import registry_fixtures as fx

    path = fx.write_registry(
        tmp_path, entries={"a-1": fx.minimal_entry(transport="stdio")}
    )
    with pytest.raises(agent_registry.RegistryRefusal) as excinfo:
        agent_registry.load_agents_file(path)
    assert excinfo.value.rule == "REGISTRY_UNKNOWN_KEY"


def test_one_managed_process_per_run_from_spawn_to_reap():
    """The supervision seam is singular and non-optional after spawn."""
    text = (NATIVE / "run_task.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    spawns = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "spawn_managed_process"
    ]
    assert len(spawns) == 1, "a Run must own exactly one spawn seam"
    assert "ctx.proc" in text and "await ctx.proc.wait()" in text or "wait()" in text


# -- A3: the filesystem boundary ---------------------------------------------


def test_no_agent_state_surface_is_managed_or_inspected():
    """ARS owns the process it started; the user owns the software it started."""
    banned = (
        ".claude/settings",
        ".codex/config.toml",
        "project_config_relpath",
        "auth.json",
        "credentials.json",
        "staged_credential",
        "agent_home",
        "managed_home",
    )
    for path, text in reset_sources():
        for name in banned:
            assert name not in text, f"{path.name} models AGENT-owned state: {name!r}"


def test_the_writable_surfaces_are_exactly_two():
    """One storage seam plus the UDS runtime path, named once each."""
    from agent_run_supervisor.native_acp import storage

    seam = Path(storage.__file__).read_text(encoding="utf-8")
    assert "NATIVE_RUNS_DIRNAME" in seam and "NATIVE_SESSIONS_DIRNAME" in seam
    server = (ARSD / "server.py").read_text(encoding="utf-8")
    assert "socket_path" in server


def test_a_registry_below_home_via_symlink_is_read_only_and_works(tmp_path, monkeypatch):
    """A dotfiles symlink is the ordinary operator layout, and ARS never writes it."""
    from agent_run_supervisor.native_acp import agent_registry

    from tests.native_acp import registry_fixtures as fx

    home = tmp_path / "home"
    dotfiles = home / ".config" / "ars"
    dotfiles.mkdir(parents=True)
    target = fx.write_registry(dotfiles)
    link = home / "agents.toml"
    link.symlink_to(target)

    before = target.stat()
    assert agent_registry.load_agents_file(link).ids() == ("native-agent",)
    after = target.stat()
    assert (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)


def test_a_command_below_home_via_path_shim_is_launchable(tmp_path):
    """A2/A3 together: the command may live anywhere, and ARS stats none of it."""
    import asyncio

    from agent_run_supervisor.managed_process import (
        ManagedProcessLimits,
        spawn_managed_process,
    )

    home = tmp_path / "home"
    shims = home / ".local" / "bin"
    shims.mkdir(parents=True)
    (shims / "some-agent").symlink_to("/bin/sh")

    async def go():
        proc = await spawn_managed_process(
            argv=["some-agent", "-c", 'printf "ok\\n" >&2; exec cat >/dev/null'],
            cwd=tmp_path,
            env={"PATH": str(shims), "HOME": str(home)},
            limits=ManagedProcessLimits(),
        )
        proc.stdin.close()
        await proc.wait()
        return proc.stderr_bytes().decode()

    assert "ok" in asyncio.run(go())


def test_a_child_that_mutates_its_own_home_completes_normally(tmp_path):
    """The child may write its own state; ARS neither prevents nor inspects it."""
    import asyncio

    from agent_run_supervisor.managed_process import (
        ManagedProcessLimits,
        spawn_managed_process,
    )

    home = tmp_path / "home"
    home.mkdir()

    async def go():
        proc = await spawn_managed_process(
            argv=[
                "/bin/sh",
                "-c",
                'mkdir -p "$HOME/.agent-cache" && printf x > "$HOME/.agent-cache/state"'
                "; exec cat >/dev/null",
            ],
            cwd=tmp_path,
            env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
            limits=ManagedProcessLimits(),
        )
        proc.stdin.close()
        return await proc.wait()

    exit_state = asyncio.run(go())
    assert exit_state.exit_code == 0
    assert (home / ".agent-cache" / "state").read_text() == "x"


# -- the removed runtime is gone from the tree, not merely unreferenced -------
#
# The "no reset module reaches the acpx runtime" scan that lived here has moved
# into ``tools/static_safety_scan.py``, which applies the same shape rule to the
# whole repository rather than to the reset line alone, and runs inside
# ``make verify``. One scanner, so the two cannot disagree about what counts as
# reaching it. What stays here is the tombstone: the modules themselves are
# absent, which is a fact about the tree that no import scan can express.


@pytest.mark.parametrize(
    "name",
    [
        "runner.py",
        "parser.py",
        "preflight.py",
        "session_runtime.py",
        "live_stream.py",
        "policy.py",
        "role.py",
        "mcp_config.py",
        "workspace.py",
        "goal.py",
        "caller.py",
        "session_inspect.py",
        "retention.py",
    ],
)
def test_the_removed_runtime_module_is_absent_from_the_tree(name):
    assert not (SRC / name).exists(), f"{name} still exists"


@pytest.mark.parametrize("name", ["hermes_caller", "fixtures"])
def test_the_removed_package_directory_is_absent_from_the_tree(name):
    assert not (SRC / name).exists(), f"{name}/ still exists"


def test_the_removed_leaves_are_not_re_exported_under_another_name():
    """Deleted, not relocated: no surviving module offers the same entry point."""
    banned = (
        "SupervisorRunner",
        "SessionRuntime",
        "parse_acpx_stdout",
        "AgentRoleSpec",
        "load_role",
        "resolve_mcp_config",
        "plan_cleanup",
        "compile_goal_prompt",
        "CallerResult",
    )
    for path, text in reset_sources():
        for name in banned:
            assert name not in text, f"{path.name} still carries {name!r}"

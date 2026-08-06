"""B6 — the one-file, once-at-startup operator agent registry.

The registry is the operator's answer to "which command is that agent here".
It is read exactly once per daemon lifetime into an immutable snapshot, every
defect refuses the whole file by a stable rule, and no refusal message may
carry an overlay value or a raw file fragment.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from agent_run_supervisor.native_acp import agent_registry
from agent_run_supervisor.native_acp import profile as profile_mod
from agent_run_supervisor.native_acp import spec

from . import registry_fixtures as fx

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "agent_run_supervisor"
    / "native_acp"
    / "agent_registry.py"
)


def load(tmp_path, **kwargs):
    return agent_registry.load_agents_file(fx.write_registry(tmp_path, **kwargs))


def refusal(tmp_path, **kwargs) -> str:
    with pytest.raises(agent_registry.RegistryRefusal) as excinfo:
        load(tmp_path, **kwargs)
    return excinfo.value.rule


# -- the happy path ---------------------------------------------------------


def test_minimal_entry_parses_into_an_immutable_snapshot(tmp_path):
    snapshot = load(tmp_path)
    assert snapshot.ids() == ("native-agent",)
    entry = snapshot.get("native-agent")
    assert entry.agent_id == "native-agent"
    assert entry.profile_id == fx.STANDARD_PROFILE
    assert entry.command == "some-agent"
    assert entry.args == ()
    assert entry.mediation_id is None
    assert entry.env_passthrough == ()
    assert entry.env_overlay == ()
    assert entry.session_epoch is None


def test_full_entry_projects_every_declared_field(tmp_path):
    snapshot = load(tmp_path, entries={"a-1": fx.full_entry()})
    entry = snapshot.get("a-1")
    assert entry.command == "/opt/example/bin/some-agent"
    assert entry.args == ("acp",)
    assert entry.mediation_id == fx.MEDIATION_ID
    assert entry.env_passthrough == ("SSH_AUTH_SOCK", "SOME_AGENT_CONFIG")
    assert dict(entry.env_overlay) == {
        "SOME_AGENT_HOME": "/home/svc/.some-agent",
        "NO_BROWSER": "1",
    }
    assert entry.model_selector_id == "model"
    assert entry.effort_selector_id == "reasoning_effort"
    assert entry.forbidden_capabilities == ("terminal",)
    assert entry.session_epoch == 1


def test_snapshot_is_immutable_and_carries_no_reopen_seam(tmp_path):
    snapshot = load(tmp_path)
    with pytest.raises(Exception):
        snapshot.entries["native-agent"] = None  # type: ignore[index]
    assert not hasattr(snapshot, "reload")
    assert not hasattr(snapshot, "path")


# -- config hygiene ---------------------------------------------------------


def test_absent_file_refuses(tmp_path):
    with pytest.raises(agent_registry.RegistryRefusal) as excinfo:
        agent_registry.load_agents_file(tmp_path / "nope.toml")
    assert excinfo.value.rule == "REGISTRY_ABSENT"


def test_group_or_world_writable_file_refuses(tmp_path):
    for mode in (0o620, 0o602, 0o666):
        path = fx.write_registry(tmp_path, name=f"m{mode:o}.toml", mode=mode)
        with pytest.raises(agent_registry.RegistryRefusal) as excinfo:
            agent_registry.load_agents_file(path)
        assert excinfo.value.rule == "REGISTRY_UNSAFE_MODE"


def test_non_regular_resolved_target_refuses(tmp_path):
    directory = tmp_path / "as-a-dir"
    directory.mkdir()
    with pytest.raises(agent_registry.RegistryRefusal) as excinfo:
        agent_registry.load_agents_file(directory)
    assert excinfo.value.rule == "REGISTRY_NOT_REGULAR_FILE"


def test_symlink_to_a_safe_regular_file_is_admitted(tmp_path):
    """A dotfiles symlink is the ordinary operator layout, including below $HOME."""
    real = tmp_path / "dotfiles"
    real.mkdir()
    target = fx.write_registry(real, name="agents.toml")
    link = tmp_path / "agents-link.toml"
    link.symlink_to(target)
    assert agent_registry.load_agents_file(link).ids() == ("native-agent",)


def test_oversize_file_refuses(tmp_path):
    filler = "# " + "x" * 4096 + "\n"
    padding = filler * ((agent_registry.MAX_REGISTRY_BYTES // len(filler)) + 2)
    assert refusal(tmp_path, text=padding + fx.registry_text()) == "REGISTRY_TOO_LARGE"


# -- document-level grammar -------------------------------------------------


def test_undecodable_toml_refuses(tmp_path):
    assert refusal(tmp_path, text="schema_version = = 1\n") == "REGISTRY_PARSE"


def test_missing_or_wrong_schema_version_refuses(tmp_path):
    assert refusal(tmp_path, schema_version=None) == "REGISTRY_SCHEMA_VERSION"
    assert refusal(tmp_path, schema_version=3) == "REGISTRY_SCHEMA_VERSION"
    assert refusal(tmp_path, text='schema_version = "1"\n') == "REGISTRY_SCHEMA_VERSION"


def test_unknown_top_level_key_refuses(tmp_path):
    assert (
        refusal(tmp_path, extra_lines=['registry_name = "prod"'])
        == "REGISTRY_UNKNOWN_KEY"
    )


@pytest.mark.parametrize(
    "unknown",
    ["transport", "secret_refs", "version_probe", "registered_models", "default_model"],
)
def test_unknown_entry_key_refuses_including_transport(tmp_path, unknown):
    """A12: ``transport`` is refused as an unknown key, not modelled as one-valued."""
    entry = fx.minimal_entry(**{unknown: "anything"})
    assert refusal(tmp_path, entries={"a-1": entry}) == "REGISTRY_UNKNOWN_KEY"


def test_entry_missing_a_required_field_refuses(tmp_path):
    assert (
        refusal(tmp_path, entries={"a-1": {"profile": fx.STANDARD_PROFILE}})
        == "ENTRY_FIELD_MISSING"
    )
    assert (
        refusal(tmp_path, entries={"a-1": {"command": "x"}}) == "ENTRY_FIELD_MISSING"
    )


def test_agents_table_must_be_a_table_of_tables(tmp_path):
    assert refusal(tmp_path, text="schema_version = 1\nagents = 3\n") == "REGISTRY_PARSE"


# -- bounds -----------------------------------------------------------------


@pytest.mark.parametrize(
    "agent_id",
    ["Upper", "-leading", ".leading", "with space", "a" * 65, "", "a/b", "a:b"],
)
def test_agent_id_grammar(tmp_path, agent_id):
    entries = {agent_id: fx.minimal_entry()}
    assert refusal(tmp_path, entries=entries) == "AGENT_ID_INVALID"


def test_agent_id_accepts_the_declared_grammar(tmp_path):
    good = "a0.b-c_" + "d" * 57
    assert len(good) == 64
    snapshot = load(tmp_path, entries={good: fx.minimal_entry()})
    assert snapshot.ids() == (good,)


def test_unknown_profile_refuses(tmp_path):
    entry = fx.minimal_entry(profile="not-a-registered-profile")
    assert refusal(tmp_path, entries={"a-1": entry}) == "ENTRY_UNKNOWN_PROFILE"


@pytest.mark.parametrize(
    "command", ["", "rel/path", "./x", "../x", "x" * 4097, "a b/c"]
)
def test_command_grammar(tmp_path, command):
    entry = fx.minimal_entry(command=command)
    assert refusal(tmp_path, entries={"a-1": entry}) == "ENTRY_COMMAND_INVALID"


def test_nul_bytes_are_refused_at_both_layers(tmp_path):
    """TOML cannot even express a NUL, and the grammar refuses one anyway.

    The document layer is not the grammar's excuse for skipping the check: the
    same grammar serves ``agents validate`` and any already-decoded document.
    """
    assert refusal(tmp_path, entries={"a-1": fx.minimal_entry(command="a\x00b")}) == (
        "REGISTRY_PARSE"
    )
    for field, value, rule in (
        ("command", "a\x00b", "ENTRY_COMMAND_INVALID"),
        ("args", ["a\x00b"], "ENTRY_ARG_TOKEN_INVALID"),
    ):
        with pytest.raises(agent_registry.RegistryRefusal) as excinfo:
            agent_registry.parse_registry_document(
                {
                    "schema_version": agent_registry.REGISTRY_SCHEMA_VERSION,
                    "agents": {"a-1": fx.minimal_entry(**{field: value})},
                }
            )
        assert excinfo.value.rule == rule


def test_command_accepts_a_bare_name_or_an_absolute_path(tmp_path):
    snapshot = load(
        tmp_path,
        entries={
            "bare": fx.minimal_entry(command="some-agent"),
            "abs": fx.minimal_entry(command="/usr/local/bin/some agent"),
        },
    )
    assert snapshot.get("bare").command == "some-agent"
    assert snapshot.get("abs").command == "/usr/local/bin/some agent"


@pytest.mark.parametrize("args", [["a"] * 33, ["x" * 1025]])
def test_args_bounds(tmp_path, args):
    entry = fx.minimal_entry(args=args)
    assert refusal(tmp_path, entries={"a-1": entry}) == "ENTRY_ARG_TOKEN_INVALID"


def test_an_empty_arg_token_survives_the_whole_file_read(tmp_path):
    """C — end to end, from operator TOML to the argv the spawn seam receives."""
    entry = fx.minimal_entry(args=["--label", "", "--end"])
    snapshot = load(tmp_path, entries={"a-1": entry})
    assert snapshot.get("a-1").argv()[1:] == ("--label", "", "--end")


@pytest.mark.parametrize("name", ["9BAD", "with-dash", "with space", "", "a" * 300])
def test_env_passthrough_name_grammar(tmp_path, name):
    entry = fx.minimal_entry(env_passthrough=[name])
    assert refusal(tmp_path, entries={"a-1": entry}) == "ENTRY_ENV_KEY_INVALID"


def test_env_passthrough_bounds(tmp_path):
    entry = fx.minimal_entry(env_passthrough=[f"NAME_{i}" for i in range(33)])
    assert refusal(tmp_path, entries={"a-1": entry}) == "ENTRY_ENV_KEY_INVALID"


def test_env_overlay_key_and_value_bounds(tmp_path):
    bad_key = fx.minimal_entry(env_overlay={"9BAD": "x"})
    assert refusal(tmp_path, entries={"a-1": bad_key}) == "ENTRY_ENV_KEY_INVALID"
    too_many = fx.minimal_entry(
        env_overlay={f"NAME_{i}": "x" for i in range(33)}
    )
    assert refusal(tmp_path, entries={"a-1": too_many}) == "ENTRY_ENV_KEY_INVALID"
    long_value = fx.minimal_entry(env_overlay={"NAME": "v" * 4097})
    assert refusal(tmp_path, entries={"a-1": long_value}) == "ENTRY_ENV_VALUE_INVALID"
    unprintable = fx.minimal_entry(env_overlay={"NAME": "a\nb"})
    assert refusal(tmp_path, entries={"a-1": unprintable}) == "ENTRY_ENV_VALUE_INVALID"


def test_forbidden_capabilities_bounds(tmp_path):
    entry = fx.minimal_entry(forbidden_capabilities=[f"cap{i}" for i in range(17)])
    assert refusal(tmp_path, entries={"a-1": entry}) == "ENTRY_CAPABILITY_INVALID"


def test_selector_hints_are_ids_not_domains(tmp_path):
    entry = fx.minimal_entry(model_selector="not a selector id")
    assert refusal(tmp_path, entries={"a-1": entry}) == "ENTRY_SELECTOR_INVALID"


@pytest.mark.parametrize("epoch", [0, -1, True])
def test_session_epoch_must_be_a_positive_integer(tmp_path, epoch):
    entry = fx.minimal_entry(session_epoch=epoch)
    assert refusal(tmp_path, entries={"a-1": entry}) == "ENTRY_SESSION_EPOCH_INVALID"


def test_unknown_mediation_id_refuses(tmp_path):
    entry = fx.minimal_entry(mediation="invent-your-own-v1")
    assert refusal(tmp_path, entries={"a-1": entry}) == "ENTRY_UNKNOWN_MEDIATION_ID"


# -- refusal hygiene --------------------------------------------------------


def test_refusal_messages_never_echo_an_overlay_value(tmp_path):
    secret = "s3cr3t-overlay-literal"
    entry = fx.minimal_entry(env_overlay={"NAME": secret + "\n"})
    with pytest.raises(agent_registry.RegistryRefusal) as excinfo:
        load(tmp_path, entries={"a-1": entry})
    assert secret not in str(excinfo.value)
    assert secret not in excinfo.value.message
    assert excinfo.value.rule == "ENTRY_ENV_VALUE_INVALID"


def test_refusal_names_the_field_path_and_the_environment_name(tmp_path):
    entry = fx.minimal_entry(env_overlay={"OVERLAY_NAME": "v" * 4097})
    with pytest.raises(agent_registry.RegistryRefusal) as excinfo:
        load(tmp_path, entries={"a-1": entry})
    assert "a-1" in excinfo.value.message
    assert "OVERLAY_NAME" in excinfo.value.message


# -- the whole file, never partially honored --------------------------------


def test_one_bad_entry_refuses_the_whole_file(tmp_path):
    entries = {
        "good": fx.minimal_entry(),
        "bad": fx.minimal_entry(command="rel/path"),
    }
    assert refusal(tmp_path, entries=entries) == "ENTRY_COMMAND_INVALID"


# -- A13: exactly one open per snapshot, none afterwards --------------------


def test_snapshot_resolution_performs_zero_filesystem_access(tmp_path, monkeypatch):
    path = fx.write_registry(tmp_path, entries={"a-1": fx.full_entry()})
    snapshot = agent_registry.load_agents_file(path)

    def refuse(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("registry resolution touched the filesystem")

    monkeypatch.setattr(os, "open", refuse)
    monkeypatch.setattr(os, "stat", refuse)
    monkeypatch.setattr(os, "lstat", refuse)
    for _ in range(3):
        assert snapshot.get("a-1").command == "/opt/example/bin/some-agent"
    assert snapshot.ids() == ("a-1",)


def test_load_opens_the_registry_exactly_once(tmp_path):
    path = fx.write_registry(tmp_path)
    opened: list[str] = []
    real_open = os.open

    def counting_open(target, *args, **kwargs):
        opened.append(str(target))
        return real_open(target, *args, **kwargs)

    original = os.open
    os.open = counting_open  # type: ignore[assignment]
    try:
        agent_registry.load_agents_file(path)
    finally:
        os.open = original  # type: ignore[assignment]
    assert [item for item in opened if item.endswith("agents.toml")] == [str(path)]


# -- structural: the module never grows a second reader ---------------------


def test_module_has_no_write_or_repair_surface():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for banned in ("write", "repair", "promote", "rollback", "install", "create"):
        assert not any(banned in name for name in names), name_error(names, banned)


def name_error(names, banned):
    return f"registry module exposes a {banned!r}-shaped function: {sorted(names)}"


def test_module_never_imports_a_writer():
    text = SOURCE.read_text(encoding="utf-8")
    for banned in ("shutil", "tempfile", "subprocess", "os.remove", "os.rename"):
        assert banned not in text


def test_known_profile_set_is_the_source_registry_itself():
    """The registry never keeps its own copy of which profiles exist."""
    assert fx.STANDARD_PROFILE in profile_mod.DEFAULT_REGISTRY.ids()


# -- A6: mediation authority is closed --------------------------------------


def test_reserved_keys_global_is_the_union_of_every_registered_binding():
    """Global, not per-selection: which binding you chose cannot change the rule."""
    union: set[str] = set()
    for pairs in profile_mod.MEDIATION_BINDINGS.values():
        union.update(key for key, _ in pairs)
    assert set(profile_mod.RESERVED_MEDIATION_KEYS) == union
    assert union, "a mediation table with no keys would make the rule vacuous"


def test_reserved_keys_global_survives_a_second_binding(monkeypatch):
    """Adding a binding widens the reserved set without touching the rule."""
    extra = dict(profile_mod.MEDIATION_BINDINGS)
    extra["another-binding-v1"] = (("ANOTHER_MEDIATION_KEY", "ask"),)
    assert "ANOTHER_MEDIATION_KEY" not in profile_mod.RESERVED_MEDIATION_KEYS
    assert profile_mod.reserved_mediation_keys(extra) == frozenset(
        {*profile_mod.RESERVED_MEDIATION_KEYS, "ANOTHER_MEDIATION_KEY"}
    )


@pytest.mark.parametrize("selected", [None, fx.MEDIATION_ID])
def test_mediation_collision_in_overlay_refuses_regardless_of_selection(
    tmp_path, selected
):
    reserved = sorted(profile_mod.RESERVED_MEDIATION_KEYS)[0]
    entry = fx.minimal_entry(mediation=selected, env_overlay={reserved: "allow"})
    assert refusal(tmp_path, entries={"a-1": entry}) == "MEDIATION_KEY_COLLISION"


@pytest.mark.parametrize("selected", [None, fx.MEDIATION_ID])
def test_mediation_collision_in_passthrough_refuses_regardless_of_selection(
    tmp_path, selected
):
    reserved = sorted(profile_mod.RESERVED_MEDIATION_KEYS)[0]
    entry = fx.minimal_entry(mediation=selected, env_passthrough=[reserved])
    assert refusal(tmp_path, entries={"a-1": entry}) == "MEDIATION_KEY_COLLISION"


def test_mediation_collision_names_the_key_and_not_a_value(tmp_path):
    reserved = sorted(profile_mod.RESERVED_MEDIATION_KEYS)[0]
    entry = fx.minimal_entry(env_overlay={reserved: "allow-everything"})
    with pytest.raises(agent_registry.RegistryRefusal) as excinfo:
        load(tmp_path, entries={"a-1": entry})
    assert reserved in excinfo.value.message
    assert "allow-everything" not in excinfo.value.message


def test_mediation_collision_refuses_the_whole_file_not_one_entry(tmp_path):
    reserved = sorted(profile_mod.RESERVED_MEDIATION_KEYS)[0]
    entries = {
        "clean": fx.minimal_entry(),
        "colliding": fx.minimal_entry(env_overlay={reserved: "allow"}),
    }
    assert refusal(tmp_path, entries=entries) == "MEDIATION_KEY_COLLISION"


def test_offline_validate_applies_the_identical_collision_check(tmp_path):
    """``agents validate`` and the daemon call one function, so they cannot drift."""
    reserved = sorted(profile_mod.RESERVED_MEDIATION_KEYS)[0]
    text = fx.registry_text(
        entries={"a-1": fx.minimal_entry(env_overlay={reserved: "allow"})}
    )
    with pytest.raises(agent_registry.RegistryRefusal) as offline:
        agent_registry.parse_registry_text(text)
    assert offline.value.rule == "MEDIATION_KEY_COLLISION"
    assert refusal(tmp_path, text=text) == offline.value.rule


def test_there_is_no_mediation_off_and_no_per_entry_pair_form(tmp_path):
    for value in ("off", "none", "false", ""):
        entry = fx.minimal_entry(mediation=value)
        assert refusal(tmp_path, entries={"a-1": entry}) == "ENTRY_UNKNOWN_MEDIATION_ID"
    entry = fx.minimal_entry(mediation={"KEY": "value"})
    assert refusal(tmp_path, entries={"a-1": entry}) == "ENTRY_UNKNOWN_MEDIATION_ID"


def test_profile_registry_construction_asserts_base_reserved_disjointness():
    """A base allowlist name that is also reserved would give one key two owners."""
    for profile_id in profile_mod.DEFAULT_REGISTRY.ids():
        profile = profile_mod.DEFAULT_REGISTRY.get(profile_id)
        base = set(profile_mod.base_env_allowlist(profile))
        assert base.isdisjoint(profile_mod.RESERVED_MEDIATION_KEYS)


# -- A6 (iv): mediation is applied last anyway ------------------------------


def test_precedence_puts_mediation_last_even_when_the_check_is_bypassed():
    """Defense in depth: a defect in the collision check cannot disable mediation.

    The harness bypasses the parse-time refusal by building the entry directly,
    which is the only way an overlay could ever carry a reserved key. Layer 4 is
    still applied last, so the source-owned value is what the child receives.
    """
    reserved, source_value = next(iter(profile_mod.MEDIATION_BINDINGS[fx.MEDIATION_ID]))
    entry = agent_registry.AgentEntry(
        agent_id="a-1",
        profile_id=fx.STANDARD_PROFILE,
        command="some-agent",
        mediation_id=fx.MEDIATION_ID,
        env_overlay=((reserved, "operator-authored-override"),),
    )
    layers = spec.environment_layers(
        arsd_env={"HOME": "/home/svc", "PATH": "/usr/bin"},
        base_names=("HOME", "PATH"),
        entry=entry,
    )
    resolved = {name: value for name, value, _, _ in layers}
    assert resolved[reserved] == source_value
    winning = {name: (source, precedence) for name, _, source, precedence in layers}
    assert winning[reserved] == ("mediation", 4)


def test_precedence_orders_base_passthrough_overlay_mediation():
    entry = agent_registry.AgentEntry(
        agent_id="a-1",
        profile_id=fx.STANDARD_PROFILE,
        command="some-agent",
        mediation_id=fx.MEDIATION_ID,
        env_passthrough=("PATH", "SOME_AGENT_CONFIG"),
        env_overlay=(("PATH", "/operator/bin"),),
    )
    layers = spec.environment_layers(
        arsd_env={"HOME": "/home/svc", "PATH": "/usr/bin"},
        base_names=("HOME", "PATH"),
        entry=entry,
    )
    by_name = {name: (value, source, precedence) for name, value, source, precedence in layers}
    assert by_name["HOME"] == ("/home/svc", "base", 1)
    # Declared in both layer 2 and layer 3: the later layer wins and is what the
    # evidence records, because precedence is the *winning* layer, not a history.
    assert by_name["PATH"] == ("/operator/bin", "overlay", 3)
    assert "SOME_AGENT_CONFIG" not in by_name


def test_precedence_declares_absent_operator_names_without_inventing_values():
    entry = agent_registry.AgentEntry(
        agent_id="a-1",
        profile_id=fx.STANDARD_PROFILE,
        command="some-agent",
        env_passthrough=("SOME_AGENT_CONFIG",),
    )
    layers = spec.environment_layers(
        arsd_env={"HOME": "/home/svc"}, base_names=("HOME", "PATH"), entry=entry
    )
    assert [name for name, _, _, _ in layers] == ["HOME"]
    assert spec.declared_absent_names(
        arsd_env={"HOME": "/home/svc"}, entry=entry
    ) == ("SOME_AGENT_CONFIG",)


def test_precedence_omits_empty_base_values_only_when_absent():
    """Present-but-empty is a real declaration; absent is not."""
    entry = agent_registry.AgentEntry(
        agent_id="a-1", profile_id=fx.STANDARD_PROFILE, command="some-agent"
    )
    layers = spec.environment_layers(
        arsd_env={"HOME": ""}, base_names=("HOME", "PATH"), entry=entry
    )
    assert layers == (("HOME", "", "base", 1),)

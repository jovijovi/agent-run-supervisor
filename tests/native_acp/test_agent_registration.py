"""The operator agent-registry **entry** grammar, driven directly.

The leaf module is *pure*: it decides what an entry may say without ever asking
the filesystem anything. That is what makes it unraceable and unredirectable —
a grammar that decides on text alone cannot be made to disagree with what a
later reader sees.

The V4 reset replaced what an operator declaration *is*. The retired grammar
carried a frozen contract identity, source-narrowed value domains, credential
slots, a registration hash used as a freeze, and provenance receipts; all five
belonged to the artifact/Binding layer and went with it. What an entry carries
now is the command and its argv, the environment names and literals, a
mediation *selection*, selector-id hints, a capability narrowing, and an
optional continuity epoch — and nothing else, so the refusal of anything else
is structural rather than filtered.

:mod:`tests.native_acp.test_agent_registry` drives this grammar through a real
file. This suite drives it directly, which is where the *pure* half of the
contract — no filesystem, no hash, no identity — can actually be pinned.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor.native_acp import agent_registration as ar
from agent_run_supervisor.native_acp import spec as spec_mod
from agent_run_supervisor.native_acp.profile import (
    DEFAULT_REGISTRY,
    MEDIATION_BINDING_IDS,
)

LEAF_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "agent_run_supervisor"
    / "native_acp"
    / "agent_registration.py"
)

KNOWN_PROFILES = frozenset(DEFAULT_REGISTRY.ids())
MEDIATION_ID = "ask-privileged-tool-families-v1"


def payload(**overrides: Any) -> dict[str, Any]:
    """An entry an operator could author against the live registry contract."""
    body: dict[str, Any] = {
        "profile": "standard-native-acp-v1",
        "command": "some-agent",
        "args": ["acp"],
        "mediation": MEDIATION_ID,
        "env_passthrough": ["SSH_AUTH_SOCK"],
        "env_overlay": {"SOME_AGENT_HOME": "/home/svc/.some-agent"},
        "model_selector": "model",
        "effort_selector": "reasoning_effort",
        "forbidden_capabilities": ["terminal"],
        "session_epoch": 1,
    }
    body.update(overrides)
    return body


def parse(body: Any, *, agent_id: str = "fake-alpha") -> ar.AgentEntry:
    return ar.parse_entry(
        body,
        agent_id=agent_id,
        known_profile_ids=KNOWN_PROFILES,
        known_mediation_ids=MEDIATION_BINDING_IDS,
    )


def refusal(body: Any, *, agent_id: str = "fake-alpha") -> str:
    with pytest.raises(ar.RegistryRefusal) as excinfo:
        parse(body, agent_id=agent_id)
    return excinfo.value.rule


# -- happy path --------------------------------------------------------------


def test_a_well_formed_entry_resolves_every_declared_fact() -> None:
    entry = parse(payload())
    assert entry.agent_id == "fake-alpha"
    assert entry.profile_id == "standard-native-acp-v1"
    assert entry.command == "some-agent"
    assert entry.args == ("acp",)
    assert entry.mediation_id == MEDIATION_ID
    assert entry.env_passthrough == ("SSH_AUTH_SOCK",)
    assert dict(entry.env_overlay) == {"SOME_AGENT_HOME": "/home/svc/.some-agent"}
    assert entry.model_selector_id == "model"
    assert entry.effort_selector_id == "reasoning_effort"
    assert entry.forbidden_capabilities == ("terminal",)
    assert entry.session_epoch == 1


def test_only_profile_and_command_are_required() -> None:
    entry = parse({"profile": "standard-native-acp-v1", "command": "some-agent"})
    assert entry.args == ()
    assert entry.mediation_id is None
    assert entry.env_passthrough == ()
    assert entry.env_overlay == ()
    assert entry.model_selector_id is None
    assert entry.effort_selector_id is None
    assert entry.forbidden_capabilities == ()
    assert entry.session_epoch is None


def test_argv_is_the_command_followed_by_its_declared_args() -> None:
    """``argv[0]`` is the declared command string, byte for byte."""
    entry = parse(payload(command="some-agent", args=["acp", "--stdio"]))
    assert entry.argv() == ("some-agent", "acp", "--stdio")
    assert entry.argv()[0] == entry.command


# -- the closed field set ----------------------------------------------------


def test_the_field_set_is_closed_and_named_once() -> None:
    assert ar.REQUIRED_ENTRY_FIELDS == ("profile", "command")
    assert set(ar.ENTRY_FIELDS) == set(ar.REQUIRED_ENTRY_FIELDS) | set(
        ar.OPTIONAL_ENTRY_FIELDS
    )
    assert len(ar.ENTRY_FIELDS) == len(set(ar.ENTRY_FIELDS))


@pytest.mark.parametrize(
    "field",
    [
        "transport",
        "secret_refs",
        "version_probe",
        "registered_models",
        "allowed_efforts",
        "default_model",
        "default_effort",
        "contract_identity",
        "adapter_contract_hash",
        "provenance",
        "credentials",
        "acp",
        "sha256",
        "package_root",
    ],
)
def test_a_retired_or_unknown_field_is_refused(field: str) -> None:
    """Each of these was either a retired-layer field or was never one."""
    assert field not in ar.ENTRY_FIELDS
    assert refusal(payload(**{field: "x"})) == "REGISTRY_UNKNOWN_KEY"


@pytest.mark.parametrize("field", list(ar.REQUIRED_ENTRY_FIELDS))
def test_a_missing_required_field_is_refused(field: str) -> None:
    body = payload()
    del body[field]
    assert refusal(body) == "ENTRY_FIELD_MISSING"


def test_an_entry_that_is_not_a_table_is_refused() -> None:
    assert refusal([]) == "REGISTRY_PARSE"


# -- agent id ----------------------------------------------------------------


@pytest.mark.parametrize(
    "agent_id", ["Upper", "-leading", ".leading", "with space", "a" * 65, "", "a/b"]
)
def test_an_agent_id_outside_the_grammar_is_refused(agent_id: str) -> None:
    assert refusal(payload(), agent_id=agent_id) == "AGENT_ID_INVALID"


def test_the_agent_id_grammar_is_one_function() -> None:
    """Shared by the parse and by per-Run admission, so they cannot drift."""
    assert ar.validate_agent_id("a-1") == "a-1"
    with pytest.raises(ar.RegistryRefusal):
        ar.validate_agent_id("Not-Valid")


# -- profile and mediation selection ------------------------------------------


def test_an_unregistered_profile_is_refused() -> None:
    assert refusal(payload(profile="codex-acp-1.1.7")) == "ENTRY_UNKNOWN_PROFILE"


def test_an_unregistered_mediation_id_is_refused() -> None:
    assert refusal(payload(mediation="invent-your-own-v1")) == (
        "ENTRY_UNKNOWN_MEDIATION_ID"
    )


def test_selecting_no_mediation_binding_is_admissible() -> None:
    body = payload()
    del body["mediation"]
    assert parse(body).mediation_id is None


def test_an_entry_can_never_author_a_mediation_pair() -> None:
    """Key and value stay source owned; selection is the only operator surface."""
    assert refusal(payload(mediation={"KEY": "value"})) == "ENTRY_UNKNOWN_MEDIATION_ID"
    fields = {field.name for field in dataclasses.fields(ar.AgentEntry)}
    assert "mediation_env" not in fields and "permission_env" not in fields


# -- command -----------------------------------------------------------------


@pytest.mark.parametrize(
    "command", ["", "rel/path", "./x", "../x", "a b/c", "x" * 4097]
)
def test_a_command_outside_the_grammar_is_refused(command: str) -> None:
    assert refusal(payload(command=command)) == "ENTRY_COMMAND_INVALID"


def test_a_nul_in_the_command_is_refused_by_the_grammar_itself() -> None:
    """TOML cannot express one; the grammar refuses it anyway."""
    assert refusal(payload(command="a\x00b")) == "ENTRY_COMMAND_INVALID"


def test_a_bare_name_and_an_absolute_path_are_both_admitted() -> None:
    assert parse(payload(command="some-agent")).command == "some-agent"
    assert parse(payload(command="/usr/local/bin/x")).command == "/usr/local/bin/x"


# -- args --------------------------------------------------------------------


@pytest.mark.parametrize(
    "args", [["a"] * 33, ["x" * 1025], ["a\x00b"], "not-a-list", [1]]
)
def test_args_outside_the_bounds_are_refused(args: Any) -> None:
    assert refusal(payload(args=args)) == "ENTRY_ARG_TOKEN_INVALID"


def test_an_arg_token_may_be_anything_a_shell_would_pass() -> None:
    """Passed as an argv list, never through a shell, so no quoting rule exists."""
    entry = parse(payload(args=["--flag=value with spaces", "a;b", "$HOME"]))
    assert entry.args == ("--flag=value with spaces", "a;b", "$HOME")


def test_an_empty_arg_token_is_a_legitimate_argv_token() -> None:
    """C — the contract bounds ``args`` by count, bytes, and NUL. Not by emptiness.

    ``argv`` is handed to ``exec`` directly and never to a shell, so ``""`` is an
    ordinary token: it is exactly how an operator passes an empty positional to a
    CLI that distinguishes "absent" from "present and empty". Refusing it invented
    a rule the registry contract does not state and silently changed the argv the
    operator declared.
    """
    entry = parse(payload(args=["--label", "", "--end"]))
    assert entry.args == ("--label", "", "--end")
    assert entry.argv() == ("some-agent", "--label", "", "--end")


def test_the_command_itself_still_may_not_be_empty() -> None:
    """Accepting an empty *token* does not weaken ``argv[0]``."""
    assert refusal(payload(command="")) is not None


# -- environment -------------------------------------------------------------


@pytest.mark.parametrize("name", ["9BAD", "with-dash", "with space", "", "a" * 300])
def test_an_environment_name_outside_the_grammar_is_refused(name: str) -> None:
    assert refusal(payload(env_passthrough=[name])) == "ENTRY_ENV_KEY_INVALID"
    assert refusal(payload(env_overlay={name: "x"})) == "ENTRY_ENV_KEY_INVALID"


def test_environment_declarations_are_bounded() -> None:
    many = [f"NAME_{i}" for i in range(33)]
    assert refusal(payload(env_passthrough=many)) == "ENTRY_ENV_KEY_INVALID"
    assert (
        refusal(payload(env_overlay={name: "x" for name in many}))
        == "ENTRY_ENV_KEY_INVALID"
    )


def test_a_repeated_passthrough_name_is_refused() -> None:
    assert refusal(payload(env_passthrough=["PATH", "PATH"])) == "ENTRY_ENV_KEY_INVALID"


# -- the pass-through domain is one rule, and both directions use it ----------
#
# ``declared_absent`` in a launch record is a subset of the entry's own
# ``env_passthrough``, so the reader that has to recognise a production record
# needs the identical answer the parser gave. Two implementations of "identical"
# is how a reader ends up admitting a document no writer can emit.

PASSTHROUGH_DOMAIN_CASES = [
    pytest.param([], True, id="empty"),
    pytest.param(["PATH"], True, id="one"),
    pytest.param(["A_NAME", "B_NAME"], True, id="two-distinct"),
    pytest.param([f"NAME_{i}" for i in range(ar.MAX_ENV_PASSTHROUGH)], True, id="at-bound"),
    pytest.param(
        [f"NAME_{i}" for i in range(ar.MAX_ENV_PASSTHROUGH + 1)], False, id="over-bound"
    ),
    pytest.param(["PATH", "PATH"], False, id="repeated"),
    pytest.param(["with-dash"], False, id="bad-grammar"),
    pytest.param(["9BAD"], False, id="leading-digit"),
    pytest.param([""], False, id="empty-name"),
    pytest.param(["a" * 300], False, id="over-length"),
    pytest.param([7], False, id="not-a-string"),
    pytest.param("PATH", False, id="not-a-list"),
    pytest.param(None, False, id="none"),
]


@pytest.mark.parametrize("value,accepted", PASSTHROUGH_DOMAIN_CASES)
def test_the_passthrough_domain_predicate_matches_the_parser(value, accepted) -> None:
    """Whatever the parser admits, the predicate admits — and the reverse."""
    assert ar.is_env_passthrough_domain(value) is accepted
    if not isinstance(value, list):
        return
    if accepted:
        assert parse(payload(env_passthrough=value)).env_passthrough == tuple(value)
    else:
        assert refusal(payload(env_passthrough=value)) == "ENTRY_ENV_KEY_INVALID"


def test_the_bound_is_stated_once() -> None:
    """A second literal ``32`` would be a second bound waiting to disagree."""
    source = Path(ar.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assigned = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "MAX_ENV_PASSTHROUGH" in assigned
    # Every other use of the bound is by name, in both this module and the
    # launch reader that shares it.
    reader = Path(spec_mod.__file__).read_text(encoding="utf-8")
    assert "MAX_ENV_PASSTHROUGH" not in reader
    assert "is_env_passthrough_domain" in reader


@pytest.mark.parametrize("value", ["v" * 4097, "a\nb", "a\tb", 7, None])
def test_an_overlay_value_outside_the_bounds_is_refused(value: Any) -> None:
    assert refusal(payload(env_overlay={"NAME": value})) == "ENTRY_ENV_VALUE_INVALID"


def test_a_refusal_never_quotes_an_overlay_value() -> None:
    secret = "s3cr3t-overlay-literal"
    with pytest.raises(ar.RegistryRefusal) as excinfo:
        parse(payload(env_overlay={"NAME": secret + "\n"}))
    assert secret not in excinfo.value.message
    assert "NAME" in excinfo.value.message


def test_declared_environment_names_covers_both_declarations() -> None:
    entry = parse(payload(env_passthrough=["A_NAME"], env_overlay={"B_NAME": "x"}))
    assert set(ar.declared_environment_names(entry)) == {"A_NAME", "B_NAME"}


# -- selectors, capabilities, epoch ------------------------------------------


@pytest.mark.parametrize("selector", ["not a selector id", "9bad", "", "a" * 65, 7])
def test_a_selector_hint_outside_the_grammar_is_refused(selector: Any) -> None:
    assert refusal(payload(model_selector=selector)) == "ENTRY_SELECTOR_INVALID"


def test_a_selector_hint_is_an_id_and_never_a_value_domain() -> None:
    """Live discovery is the domain authority; exact readback is the proof."""
    assert refusal(payload(model_selector=["a", "b"])) == "ENTRY_SELECTOR_INVALID"


def test_forbidden_capabilities_are_bounded_and_deduplicated() -> None:
    many = [f"cap{i}" for i in range(17)]
    assert refusal(payload(forbidden_capabilities=many)) == "ENTRY_CAPABILITY_INVALID"
    entry = parse(payload(forbidden_capabilities=["terminal", "terminal", "fs"]))
    assert entry.forbidden_capabilities == ("fs", "terminal")


@pytest.mark.parametrize("epoch", [0, -1, True, "1", 1.0])
def test_a_session_epoch_that_is_not_a_positive_integer_is_refused(epoch: Any) -> None:
    assert refusal(payload(session_epoch=epoch)) == "ENTRY_SESSION_EPOCH_INVALID"


# -- what an entry deliberately is not ---------------------------------------


def test_an_entry_carries_no_identity_hash() -> None:
    """A fingerprint-as-gate is the failure mode the reset removes.

    An agent upgrade behind an unchanged registered command must cost no ARS
    action at all, so there is nothing here for a Session identity to derive
    from except the operator's own ``session_epoch``.
    """
    fields = {field.name for field in dataclasses.fields(ar.AgentEntry)}
    for banned in (
        "registration_hash",
        "entry_hash",
        "adapter_contract_hash",
        "sha256",
        "digest",
        "version",
    ):
        assert banned not in fields
    assert not any(name.endswith("_hash") for name in fields)


def test_the_module_computes_no_hash_at_all() -> None:
    source = LEAF_SOURCE.read_text(encoding="utf-8")
    for banned in ("hashlib", "sha256", "blake2", "md5"):
        assert banned not in source


def test_the_value_blind_projection_reports_names_and_never_values() -> None:
    entry = parse(payload(env_overlay={"SOME_AGENT_HOME": "/home/svc/.secret-path"}))
    projection = ar.entry_projection(entry)
    assert projection["env_overlay_names"] == ["SOME_AGENT_HOME"]
    assert "/home/svc/.secret-path" not in str(projection)
    assert "env_overlay" not in projection


# -- purity ------------------------------------------------------------------


def test_the_entry_grammar_queries_no_filesystem() -> None:
    """The grammar decides on text, so it can never be raced or redirected."""
    source = LEAF_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned_calls = {"open", "stat", "lstat", "listdir", "readlink", "exists", "Path"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                assert name.split(".")[0] not in {
                    "os",
                    "pathlib",
                    "shutil",
                    "subprocess",
                    "tomllib",
                }, name
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            assert name not in banned_calls, f"{name} queries the filesystem"


def test_the_grammar_takes_its_known_sets_as_arguments() -> None:
    """It holds no opinion about which profiles source happens to register."""
    import inspect

    parameters = inspect.signature(ar.parse_entry).parameters
    assert "known_profile_ids" in parameters
    assert "known_mediation_ids" in parameters
    source = LEAF_SOURCE.read_text(encoding="utf-8")
    assert "from .profile import" not in source
    assert "DEFAULT_REGISTRY" not in source

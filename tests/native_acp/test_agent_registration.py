"""G3 — the operator Agent Registration grammar (closure §5.2).

The leaf module is *pure*: it decides what a registration may say without ever
asking the filesystem anything. Everything an operator can supply here either
selects inside a source-declared bound or narrows one; nothing widens.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor.native_acp import agent_registration as ar
from agent_run_supervisor.native_acp.profile import STANDARD_NATIVE_ACP_V1

LEAF_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "agent_run_supervisor"
    / "native_acp"
    / "agent_registration.py"
)


def payload(**overrides: Any) -> dict[str, Any]:
    """A registration an operator could author against the live v1 contract."""
    body: dict[str, Any] = {
        "schema_version": ar.REGISTRATION_SCHEMA_VERSION,
        "agent_id": "fake-alpha",
        "contract_identity": {
            "profile_id": STANDARD_NATIVE_ACP_V1.profile_id,
            "profile_revision": STANDARD_NATIVE_ACP_V1.revision,
            "adapter_contract_hash": STANDARD_NATIVE_ACP_V1.adapter_contract_hash(),
        },
        "acp": {"agent_name": "FakeAlpha", "forbidden_capabilities": ["terminal"]},
        "launch": {
            "argv_tokens": ["acp"],
            "version_probe_argv_suffix": ["--version"],
            "permission_binding_id": "ask-privileged-tool-families-v1",
        },
        "config": {
            "model_selector_id": "model",
            "effort_selector_id": "effort",
            "registered_models": ["alpha/one"],
            "allowed_efforts": ["low", "high"],
            "default_model": "alpha/one",
            "default_effort": "high",
        },
        "credentials": {"slots": ["alpha-auth"], "required_refs": ["alpha-auth"]},
        "provenance": {
            "created_at": "2026-07-29T09:00:00+08:00",
            "accepted_by": "operator",
            "accepted_at": "2026-07-29T09:00:00+08:00",
            "acceptance_receipt": {"ref": "receipt:accept", "sha256": "a" * 64},
            "discovery_receipt": {"ref": "receipt:discovery", "sha256": "b" * 64},
            "permission_canary_receipt": {"ref": "receipt:canary", "sha256": "c" * 64},
        },
    }
    body.update(overrides)
    return body


def parse(body: dict[str, Any], *, agent_id: str = "fake-alpha") -> ar.AgentRegistration:
    return ar.parse_registration(
        body, profile=STANDARD_NATIVE_ACP_V1, expected_agent_id=agent_id
    )


def refusal(body: dict[str, Any], *, agent_id: str = "fake-alpha") -> str:
    with pytest.raises(ar.AgentRegistrationError) as excinfo:
        parse(body, agent_id=agent_id)
    return excinfo.value.rule


# -- happy path --------------------------------------------------------------


def test_a_well_formed_registration_resolves_every_declared_fact() -> None:
    registration = parse(payload())
    assert registration.agent_id == "fake-alpha"
    assert registration.acp_agent_name == "FakeAlpha"
    assert registration.argv_tokens == ("acp",)
    assert registration.forbidden_capabilities == ("terminal",)
    assert registration.permission_binding_id == "ask-privileged-tool-families-v1"
    assert registration.registered_models == ("alpha/one",)
    assert registration.allowed_efforts == ("low", "high")
    assert registration.credential_slots == ("alpha-auth",)
    assert registration.required_credential_refs == ("alpha-auth",)
    assert len(registration.registration_hash) == 64


def test_the_registration_hash_excludes_provenance_only() -> None:
    """A re-recorded receipt must not retire an agent's Sessions; a real edit must."""
    base = parse(payload()).registration_hash
    provenance = dict(payload()["provenance"])
    provenance["accepted_at"] = "2026-08-01T09:00:00+08:00"
    assert parse(payload(provenance=provenance)).registration_hash == base

    launch = dict(payload()["launch"])
    launch["argv_tokens"] = ["acp", "--stdio"]
    assert parse(payload(launch=launch)).registration_hash != base


# -- shape -------------------------------------------------------------------


def test_an_unknown_top_level_field_is_refused() -> None:
    assert refusal(payload(extra="x")) == "UNKNOWN_REGISTRATION_FIELD"


def test_a_missing_top_level_field_is_refused() -> None:
    body = payload()
    del body["credentials"]
    assert refusal(body) == "REGISTRATION_FIELD_MISSING"


def test_an_unsupported_schema_version_is_refused() -> None:
    assert refusal(payload(schema_version=2)) == "REGISTRATION_SCHEMA_VERSION"


def test_an_agent_id_other_than_the_one_requested_is_refused() -> None:
    assert refusal(payload(), agent_id="fake-beta") == "REGISTRATION_AGENT_MISMATCH"


@pytest.mark.parametrize(
    "field",
    ["profile_id", "profile_revision", "adapter_contract_hash"],
)
def test_a_contract_identity_mismatch_on_any_field_is_refused(field: str) -> None:
    identity = dict(payload()["contract_identity"])
    identity[field] = "x" * 64 if isinstance(identity[field], str) else 99
    assert (
        refusal(payload(contract_identity=identity))
        == "REGISTRATION_CONTRACT_MISMATCH"
    )


# -- argv token grammar ------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        "./x",
        "/x",
        "a/b",
        "..",
        "a..b",
        "a b",
        "a=b",
        "a\x00b",
        "é",
        "x" * 33,
        "",
        "\\x",
    ],
)
def test_an_argv_token_that_could_be_a_path_or_a_shell_fragment_is_refused(
    token: str,
) -> None:
    launch = dict(payload()["launch"])
    launch["argv_tokens"] = [token]
    assert refusal(payload(launch=launch)) == "ARGV_TOKEN_UNSAFE"


@pytest.mark.parametrize("tokens", [[], ["a", "b", "c", "d", "e"], ["acp", "acp"]])
def test_argv_token_count_and_uniqueness_are_bounded(tokens: list[str]) -> None:
    launch = dict(payload()["launch"])
    launch["argv_tokens"] = tokens
    assert refusal(payload(launch=launch)) == "ARGV_TOKEN_UNSAFE"


def test_the_version_probe_suffix_reuses_the_source_owned_rule() -> None:
    launch = dict(payload()["launch"])
    launch["version_probe_argv_suffix"] = ["version"]  # not an option token
    assert refusal(payload(launch=launch)) == "VERSION_PROBE_UNSAFE"


def test_an_unregistered_permission_binding_id_is_refused() -> None:
    launch = dict(payload()["launch"])
    launch["permission_binding_id"] = "allow-everything-v1"
    assert refusal(payload(launch=launch)) == "UNKNOWN_PERMISSION_BINDING"


def test_selecting_no_permission_binding_is_admissible() -> None:
    launch = dict(payload()["launch"])
    launch["permission_binding_id"] = None
    assert parse(payload(launch=launch)).permission_binding_id is None


# -- config domains ----------------------------------------------------------


def test_selector_ids_must_be_distinct() -> None:
    config = dict(payload()["config"])
    config["effort_selector_id"] = config["model_selector_id"]
    assert refusal(payload(config=config)) == "SELECTOR_ID_UNSAFE"


@pytest.mark.parametrize("selector", ["1model", "mo del", "m" * 65, ""])
def test_a_selector_id_outside_the_grammar_is_refused(selector: str) -> None:
    config = dict(payload()["config"])
    config["model_selector_id"] = selector
    assert refusal(payload(config=config)) == "SELECTOR_ID_UNSAFE"


@pytest.mark.parametrize("domain", ["registered_models", "allowed_efforts"])
def test_an_empty_domain_is_refused(domain: str) -> None:
    config = dict(payload()["config"])
    config[domain] = []
    assert refusal(payload(config=config)) == "SELECTOR_DOMAIN_UNSAFE"


@pytest.mark.parametrize(
    ("domain", "default"),
    [("registered_models", "default_model"), ("allowed_efforts", "default_effort")],
)
def test_a_default_outside_its_own_domain_is_refused(domain: str, default: str) -> None:
    config = dict(payload()["config"])
    config[default] = "not-in-domain"
    assert refusal(payload(config=config)) == "SELECTOR_DEFAULT_UNSAFE"


def test_a_duplicated_domain_entry_is_refused() -> None:
    config = dict(payload()["config"])
    config["allowed_efforts"] = ["low", "low"]
    assert refusal(payload(config=config)) == "SELECTOR_DOMAIN_UNSAFE"


# -- capabilities: the source floor may be raised, never lowered -------------


def test_a_registration_dropping_the_contract_capability_floor_is_refused() -> None:
    acp = dict(payload()["acp"])
    acp["forbidden_capabilities"] = []
    floor = STANDARD_NATIVE_ACP_V1.contract.forbidden_capabilities
    if not floor:
        pytest.skip("the v1 contract declares an empty forbidden floor")
    assert refusal(payload(acp=acp)) == "CAPABILITY_FLOOR_LOWERED"


def test_a_registration_forbidding_a_required_capability_is_refused() -> None:
    acp = dict(payload()["acp"])
    acp["forbidden_capabilities"] = ["terminal", "loadSession"]
    assert refusal(payload(acp=acp)) == "CAPABILITY_CONFLICT"


def test_a_registration_may_forbid_more_than_the_floor() -> None:
    acp = dict(payload()["acp"])
    acp["forbidden_capabilities"] = ["terminal", "fs"]
    assert parse(payload(acp=acp)).forbidden_capabilities == ("fs", "terminal")


# -- credentials -------------------------------------------------------------


def test_required_refs_must_be_a_subset_of_the_declared_slots() -> None:
    credentials = dict(payload()["credentials"])
    credentials["required_refs"] = ["not-a-slot"]
    assert refusal(payload(credentials=credentials)) == "CREDENTIAL_REFS_UNSAFE"


def test_required_refs_may_be_null_for_an_unconstrained_agent() -> None:
    credentials = dict(payload()["credentials"])
    credentials["required_refs"] = None
    assert parse(payload(credentials=credentials)).required_credential_refs is None


# -- provenance is recorded, never consulted ---------------------------------


def test_provenance_shape_is_required() -> None:
    provenance = dict(payload()["provenance"])
    del provenance["discovery_receipt"]
    assert refusal(payload(provenance=provenance)) == "PROVENANCE_FIELD_MISSING"


def test_a_flawless_provenance_rescues_nothing() -> None:
    """C4 verbatim: the acceptance record never decides admissibility."""
    launch = dict(payload()["launch"])
    launch["argv_tokens"] = ["/usr/bin/evil"]
    assert refusal(payload(launch=launch)) == "ARGV_TOKEN_UNSAFE"


# -- purity ------------------------------------------------------------------


def test_the_registration_leaf_queries_no_filesystem() -> None:
    """G7: the grammar decides on text, so it can never be raced or redirected."""
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
                assert name.split(".")[0] not in {"os", "pathlib", "shutil", "subprocess"}, name
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            assert name not in banned_calls, f"{name} queries the filesystem"

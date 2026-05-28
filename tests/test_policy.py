from __future__ import annotations

import copy
import json

from agent_run_supervisor.policy import (
    compile_command,
    compile_permission_policy,
    policy_hash,
)
from agent_run_supervisor.role import load_role
from tests.test_role import VALID_ROLE


def _role_with(**overrides: dict) -> dict:
    spec = copy.deepcopy(VALID_ROLE)
    for key, value in overrides.items():
        spec[key] = value
    return spec


def test_compile_permission_policy_maps_kinds_to_auto_lists() -> None:
    role = load_role(VALID_ROLE)
    policy = compile_permission_policy(role)

    assert policy["defaultAction"] == "deny"
    assert "read" in policy["autoApprove"]
    assert "search" in policy["autoApprove"]
    assert "execute" in policy["autoDeny"]
    assert "delete" in policy["autoDeny"]


def test_compile_permission_policy_excludes_terminal_from_kinds() -> None:
    role = load_role(VALID_ROLE)
    policy = compile_permission_policy(role)

    assert "terminal" not in policy["autoApprove"]
    assert "terminal" not in policy["autoDeny"]


def test_compile_command_includes_v0_1a_runner_flags() -> None:
    role = load_role(VALID_ROLE)
    argv = compile_command(role, cwd="/tmp/work", prompt="hello")

    expected_flags = [
        "--format",
        "json",
        "--json-strict",
        "--suppress-reads",
        "--timeout",
        "900",
        "--max-turns",
        "1",
    ]
    for flag in expected_flags:
        assert flag in argv, argv
    assert "--non-interactive-permissions" in argv
    nip_index = argv.index("--non-interactive-permissions")
    assert argv[nip_index + 1] == "fail"


def test_compile_command_includes_no_terminal_when_role_disallows_terminal() -> None:
    role = load_role(VALID_ROLE)
    argv = compile_command(role, cwd="/tmp/work", prompt="hello")

    assert "--no-terminal" in argv


def test_compile_command_omits_no_terminal_when_role_allows_terminal() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["permissions"]["terminal"] = True
    role = load_role(spec)

    argv = compile_command(role, cwd="/tmp/work", prompt="hello")

    assert "--no-terminal" not in argv


def test_compile_command_passes_policy_json() -> None:
    role = load_role(VALID_ROLE)
    argv = compile_command(role, cwd="/tmp/work", prompt="hello")

    assert "--permission-policy" in argv
    policy_index = argv.index("--permission-policy")
    policy_json = argv[policy_index + 1]
    parsed = json.loads(policy_json)
    assert parsed["defaultAction"] == "deny"
    assert "read" in parsed["autoApprove"]


def test_compile_command_ends_with_adapter_agent_exec_prompt() -> None:
    role = load_role(VALID_ROLE)
    argv = compile_command(role, cwd="/tmp/work", prompt="check this PR")

    assert argv[-3:] == ["codex", "exec", "check this PR"]


def test_compile_command_uses_role_binary_when_provided() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["runner"]["acpx_binary"] = "/opt/acpx/bin/acpx"
    role = load_role(spec)

    argv = compile_command(role, cwd="/tmp/work", prompt="hello")

    assert argv[0] == "/opt/acpx/bin/acpx"


def test_compile_command_uses_cwd_argument_over_role_default() -> None:
    role = load_role(VALID_ROLE)
    argv = compile_command(role, cwd="/override/cwd", prompt="hello")

    cwd_index = argv.index("--cwd")
    assert argv[cwd_index + 1] == "/override/cwd"


def test_compile_command_uses_role_default_cwd_when_not_overridden() -> None:
    role = load_role(VALID_ROLE)
    argv = compile_command(role, cwd=None, prompt="hello")

    cwd_index = argv.index("--cwd")
    assert argv[cwd_index + 1] == VALID_ROLE["workspace"]["default_cwd"]


def test_policy_hash_changes_with_permissions() -> None:
    role_a = load_role(VALID_ROLE)
    spec_b = copy.deepcopy(VALID_ROLE)
    spec_b["permissions"]["write"] = True
    role_b = load_role(spec_b)

    assert policy_hash(role_a) != policy_hash(role_b)


def test_compile_permission_policy_maps_write_to_acpx_edit_kind() -> None:
    role = load_role(VALID_ROLE)
    policy = compile_permission_policy(role)

    assert "edit" in policy["autoDeny"]
    combined = set(policy["autoApprove"]) | set(policy["autoDeny"])
    assert "write" not in combined


def test_compile_permission_policy_does_not_include_terminal_in_kinds_list() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["permissions"]["terminal"] = True
    role = load_role(spec)
    policy = compile_permission_policy(role)

    assert "terminal" not in policy["autoApprove"]
    assert "terminal" not in policy["autoDeny"]

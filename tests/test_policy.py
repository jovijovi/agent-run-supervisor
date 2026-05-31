from __future__ import annotations

import copy
import json

import pytest

from agent_run_supervisor.policy import (
    ExecStrategyError,
    compile_command,
    compile_permission_policy,
    compile_session_create_command,
    compile_session_ensure_command,
    compile_session_prompt_command,
    compile_session_show_command,
    compile_session_status_command,
    ensure_persistent_strategy,
    policy_hash,
)
from agent_run_supervisor.role import load_role
from tests.test_role import VALID_ROLE


def _role_with(**overrides: dict) -> dict:
    spec = copy.deepcopy(VALID_ROLE)
    for key, value in overrides.items():
        spec[key] = value
    return spec


def _persistent_role(**overrides: dict):
    spec = copy.deepcopy(VALID_ROLE)
    spec["session"] = {"strategy": "persistent"}
    # Default to the pinned ``npx`` fetch path so the compiled prefix matches the
    # S1a fixtures (npx -y acpx@0.10.0); the binary path has its own test.
    spec["runner"] = {**spec["runner"], "acpx_binary": None}
    for key, value in overrides.items():
        spec[key] = value
    return load_role(spec)


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


# --- S1c persistent-session command compilation ---------------------------
#
# These pin the fixture-proven acpx@0.10.0 persistent-session command grammar
# (fixtures/acpx-0.10.0/session-*). Management commands carry only
# ``--format json --json-strict --cwd`` plus a management tail; prompt turns
# use the stricter S1a fixture-proven ``--deny-all`` block and end with
# ``prompt -s <name> <prompt>``.

_MANAGEMENT_PREFIX = ["npx", "-y", "acpx@0.10.0", "--format", "json", "--json-strict"]


def test_ensure_persistent_strategy_refuses_exec_role() -> None:
    role = load_role(VALID_ROLE)
    with pytest.raises(ExecStrategyError):
        ensure_persistent_strategy(role)


def test_ensure_persistent_strategy_accepts_persistent_role() -> None:
    role = _persistent_role()
    # Should not raise.
    ensure_persistent_strategy(role)


def test_compile_session_create_command_matches_fixture_grammar() -> None:
    role = _persistent_role()
    argv = compile_session_create_command(role, cwd="/tmp/work", session_name="nightly")

    assert argv[:6] == _MANAGEMENT_PREFIX
    cwd_index = argv.index("--cwd")
    assert argv[cwd_index + 1] == "/tmp/work"
    assert argv[-5:] == ["codex", "sessions", "new", "--name", "nightly"]
    # Management commands never carry exec/turn authorization flags.
    for forbidden in ("--permission-policy", "--timeout", "--max-turns", "--suppress-reads", "--no-terminal"):
        assert forbidden not in argv, forbidden


def test_compile_session_ensure_command_uses_ensure_subcommand() -> None:
    role = _persistent_role()
    argv = compile_session_ensure_command(role, cwd="/tmp/work", session_name="nightly")

    assert argv[-5:] == ["codex", "sessions", "ensure", "--name", "nightly"]
    assert "--permission-policy" not in argv


def test_compile_session_show_command_uses_show_subcommand() -> None:
    role = _persistent_role()
    argv = compile_session_show_command(role, cwd="/tmp/work", session_name="nightly")

    assert argv[-4:] == ["codex", "sessions", "show", "nightly"]
    assert "--permission-policy" not in argv


def test_compile_session_status_command_uses_status_dash_s() -> None:
    role = _persistent_role()
    argv = compile_session_status_command(role, cwd="/tmp/work", session_name="nightly")

    assert argv[-4:] == ["codex", "status", "-s", "nightly"]


def test_compile_session_prompt_command_ends_with_prompt_tail() -> None:
    role = _persistent_role()
    argv = compile_session_prompt_command(
        role, cwd="/tmp/work", session_name="nightly", prompt="say hi"
    )

    assert argv[-5:] == ["codex", "prompt", "-s", "nightly", "say hi"]


def test_compile_session_prompt_command_uses_fixture_proven_deny_all_not_policy_json() -> None:
    role = _persistent_role()
    argv = compile_session_prompt_command(
        role, cwd="/tmp/work", session_name="nightly", prompt="say hi"
    )

    # S1a only proved persistent prompt turns with --deny-all. The local
    # session record remains role/policy-bound; prompt-turn tool permission
    # expansion needs a later fixture-proven slice.
    assert "--deny-all" in argv
    assert "--permission-policy" not in argv


def test_compile_session_prompt_command_shares_exec_automation_flags() -> None:
    role = _persistent_role()
    argv = compile_session_prompt_command(
        role, cwd="/tmp/work", session_name="nightly", prompt="say hi"
    )

    for flag in (
        "--format",
        "--json-strict",
        "--suppress-reads",
        "--timeout",
        "--max-turns",
        "--cwd",
        "--deny-all",
        "--non-interactive-permissions",
        "--no-terminal",
    ):
        assert flag in argv, flag
    timeout_index = argv.index("--timeout")
    assert argv[timeout_index + 1] == str(role.limits.timeout_seconds)
    max_turns_index = argv.index("--max-turns")
    assert argv[max_turns_index + 1] == str(role.limits.max_turns)
    model_index = argv.index("--model")
    assert argv[model_index + 1] == role.runner.model


def test_compile_session_prompt_command_keeps_prompt_as_single_argv_element() -> None:
    role = _persistent_role()
    injection = "ok; rm -rf / && echo $(whoami)"
    argv = compile_session_prompt_command(
        role, cwd="/tmp/work", session_name="nightly", prompt=injection
    )

    # No shell: the whole prompt is one argv element, never split or expanded.
    assert argv[-1] == injection
    assert all(isinstance(part, str) for part in argv)


def test_compile_session_commands_use_role_binary_when_present() -> None:
    role = _persistent_role(
        runner={**copy.deepcopy(VALID_ROLE)["runner"], "acpx_binary": "/opt/acpx/bin/acpx"}
    )

    for argv in (
        compile_session_create_command(role, cwd="/tmp/work", session_name="n"),
        compile_session_show_command(role, cwd="/tmp/work", session_name="n"),
        compile_session_prompt_command(role, cwd="/tmp/work", session_name="n", prompt="hi"),
    ):
        assert argv[0] == "/opt/acpx/bin/acpx"
        assert "npx" not in argv


def test_compile_session_commands_default_cwd_to_role_default() -> None:
    role = _persistent_role()
    argv = compile_session_create_command(role, cwd=None, session_name="n")

    cwd_index = argv.index("--cwd")
    assert argv[cwd_index + 1] == VALID_ROLE["workspace"]["default_cwd"]


def test_compile_session_commands_refuse_exec_strategy_role() -> None:
    role = load_role(VALID_ROLE)  # strategy == "exec"
    for compile_fn in (
        lambda: compile_session_create_command(role, cwd="/tmp/work", session_name="n"),
        lambda: compile_session_ensure_command(role, cwd="/tmp/work", session_name="n"),
        lambda: compile_session_show_command(role, cwd="/tmp/work", session_name="n"),
        lambda: compile_session_status_command(role, cwd="/tmp/work", session_name="n"),
        lambda: compile_session_prompt_command(role, cwd="/tmp/work", session_name="n", prompt="hi"),
    ):
        with pytest.raises(ExecStrategyError):
            compile_fn()


def test_compile_session_commands_reject_empty_session_name() -> None:
    role = _persistent_role()
    with pytest.raises(ValueError):
        compile_session_create_command(role, cwd="/tmp/work", session_name="")


def test_exec_compile_command_still_refuses_persistent_role() -> None:
    # Regression: the exec compiler must keep failing closed for persistent roles.
    role = _persistent_role()
    with pytest.raises(ExecStrategyError):
        compile_command(role, cwd="/tmp/work", prompt="hello")

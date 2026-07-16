from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from agent_run_supervisor.mcp_config import McpConfigError, resolve_mcp_config
from agent_run_supervisor.policy import (
    ExecStrategyError,
    compile_command,
    compile_permission_policy,
    compile_session_cancel_command,
    compile_session_close_command,
    compile_session_create_command,
    compile_session_ensure_command,
    compile_session_prompt_command,
    compile_session_show_command,
    compile_session_status_command,
    ensure_persistent_strategy,
    policy_hash,
)
from agent_run_supervisor.role import load_role, role_hash
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
    # S1a fixtures (npx -y acpx@0.12.0); the binary path has its own test.
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


# --- Phase B: compile-command golden evidence -----------------------------
#
# Pinned-binary + adapter golden tests supporting the upcoming Sachima
# controlled-local-execution work (PRD FR-3/FR-4/FR-13, NFR-2; design packet
# [B1]). Static compiler evidence only: no acpx/npx is launched and no real
# fixture is captured. See docs/plans/archive/2026-06-12-phase-b-ars-evidence-hardening.md.


@pytest.mark.parametrize("adapter_agent", ["codex", "claude"])
def test_compile_command_pins_binary_and_emits_adapter_exec_prompt(
    adapter_agent: str,
) -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["runner"] = {
        **spec["runner"],
        "acpx_binary": "/opt/acpx/bin/acpx",
        "adapter_agent": adapter_agent,
    }
    role = load_role(spec)

    # A prompt carrying shell metacharacters proves the no-shell / argv-list
    # contract: it must survive as one untouched argv element.
    prompt = "review this; printf SAFE && echo $(whoami)"
    argv = compile_command(role, cwd="/tmp/work", prompt=prompt)

    # [B1] Pinned local binary prefix; the npx fetch fallback never appears.
    assert argv[0] == "/opt/acpx/bin/acpx"
    assert "npx" not in argv

    # FR-3 / FR-4: the command tail is exactly ``<adapter> exec <prompt>``.
    assert argv[-3:] == [adapter_agent, "exec", prompt]

    # Default-deny permission policy is preserved for both adapters.
    policy_index = argv.index("--permission-policy")
    parsed = json.loads(argv[policy_index + 1])
    assert parsed["defaultAction"] == "deny"

    # NFR-2: argv-list / no-shell — every element is a plain string and the
    # prompt stays a single element, never split or shell-expanded.
    assert all(isinstance(part, str) for part in argv)
    assert argv[-1] == prompt


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
# These pin the fixture-proven acpx@0.12.0 persistent-session command grammar
# (fixtures/acpx-0.12.0/session-*). Management commands carry only
# ``--format json --json-strict --cwd`` plus a management tail; prompt turns
# carry the shared turn authorization block and end with
# ``prompt -s <name> <prompt>``. Since S2, prompt-turn permissions are
# role-derived (``--permission-policy``); only a role granting no permission
# kinds keeps the stricter S1a ``--deny-all`` shape.

_MANAGEMENT_PREFIX = ["npx", "-y", "acpx@0.12.0", "--format", "json", "--json-strict"]


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


def test_compile_session_prompt_command_uses_role_permission_policy() -> None:
    # S2 regression: a persistent prompt turn must no longer hardcode
    # --deny-all; the role's granted permission kinds drive the compiled
    # --permission-policy JSON exactly like the exec path.
    role = _persistent_role()  # VALID_ROLE grants read + search
    argv = compile_session_prompt_command(
        role, cwd="/tmp/work", session_name="nightly", prompt="say hi"
    )

    assert "--deny-all" not in argv
    policy_index = argv.index("--permission-policy")
    parsed = json.loads(argv[policy_index + 1])
    assert parsed["defaultAction"] == "deny"
    assert "read" in parsed["autoApprove"]
    assert "search" in parsed["autoApprove"]
    assert "execute" in parsed["autoDeny"]
    nip_index = argv.index("--non-interactive-permissions")
    assert argv[nip_index + 1] == "fail"


def test_compile_session_prompt_command_keeps_deny_all_for_full_deny_role() -> None:
    # S2 regression: a role granting NO permission kinds must keep the
    # stricter S1a fixture-proven --deny-all shape and stay fail-closed.
    all_deny = {kind: False for kind in VALID_ROLE["permissions"]}
    role = _persistent_role(permissions=all_deny)
    argv = compile_session_prompt_command(
        role, cwd="/tmp/work", session_name="nightly", prompt="say hi"
    )

    assert "--deny-all" in argv
    assert "--permission-policy" not in argv
    nip_index = argv.index("--non-interactive-permissions")
    assert argv[nip_index + 1] == "fail"


def test_compile_session_prompt_command_terminal_only_role_still_denies_all() -> None:
    # ``terminal`` is not an acpx permission kind (it only controls
    # --no-terminal); a terminal-only role still grants no kinds → deny-all.
    perms = {kind: False for kind in VALID_ROLE["permissions"]}
    perms["terminal"] = True
    role = _persistent_role(permissions=perms)
    argv = compile_session_prompt_command(
        role, cwd="/tmp/work", session_name="nightly", prompt="say hi"
    )

    assert "--deny-all" in argv
    assert "--permission-policy" not in argv
    assert "--no-terminal" not in argv


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
        "--permission-policy",
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


# --- S1d close / cancel command compilation -------------------------------
#
# Fixture-proven (fixtures/acpx-0.12.0/session-close-named,
# session-cancel-no-active). Both are management commands: they carry only the
# ``--format json --json-strict --cwd`` block plus a management tail, never the
# exec/turn authorization flags, and never a shell string.


def test_compile_session_close_command_matches_fixture_grammar() -> None:
    role = _persistent_role()
    argv = compile_session_close_command(role, cwd="/tmp/work", session_name="nightly")

    assert argv[:6] == _MANAGEMENT_PREFIX
    cwd_index = argv.index("--cwd")
    assert argv[cwd_index + 1] == "/tmp/work"
    assert argv[-4:] == ["codex", "sessions", "close", "nightly"]
    for forbidden in (
        "--permission-policy",
        "--timeout",
        "--max-turns",
        "--suppress-reads",
        "--deny-all",
        "--no-terminal",
    ):
        assert forbidden not in argv, forbidden
    assert all(isinstance(part, str) for part in argv)


def test_compile_session_cancel_command_matches_fixture_grammar() -> None:
    role = _persistent_role()
    argv = compile_session_cancel_command(role, cwd="/tmp/work", session_name="nightly")

    assert argv[:6] == _MANAGEMENT_PREFIX
    cwd_index = argv.index("--cwd")
    assert argv[cwd_index + 1] == "/tmp/work"
    # ``cancel -s <name>`` (NOT ``sessions cancel``), per the S1a fixture.
    assert argv[-4:] == ["codex", "cancel", "-s", "nightly"]
    for forbidden in (
        "--permission-policy",
        "--timeout",
        "--max-turns",
        "--suppress-reads",
        "--deny-all",
        "--no-terminal",
    ):
        assert forbidden not in argv, forbidden
    assert all(isinstance(part, str) for part in argv)


def test_compile_session_close_cancel_refuse_exec_strategy_role() -> None:
    role = load_role(VALID_ROLE)  # strategy == "exec"
    with pytest.raises(ExecStrategyError):
        compile_session_close_command(role, cwd="/tmp/work", session_name="n")
    with pytest.raises(ExecStrategyError):
        compile_session_cancel_command(role, cwd="/tmp/work", session_name="n")


def test_compile_session_close_cancel_reject_empty_session_name() -> None:
    role = _persistent_role()
    with pytest.raises(ValueError):
        compile_session_close_command(role, cwd="/tmp/work", session_name="")
    with pytest.raises(ValueError):
        compile_session_cancel_command(role, cwd="/tmp/work", session_name="")


def test_compile_session_close_cancel_use_role_binary_when_present() -> None:
    role = _persistent_role(
        runner={**copy.deepcopy(VALID_ROLE)["runner"], "acpx_binary": "/opt/acpx/bin/acpx"}
    )
    for argv in (
        compile_session_close_command(role, cwd="/tmp/work", session_name="n"),
        compile_session_cancel_command(role, cwd="/tmp/work", session_name="n"),
    ):
        assert argv[0] == "/opt/acpx/bin/acpx"
        assert "npx" not in argv


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


# --- S2 hash-stability goldens (zero-migration invariant) -------------------
#
# Cross-checked on 2026-07-08 against the installed agent-run-supervisor==0.1.3
# distribution (sachima .venv, dist-info verified): ``role_hash``,
# ``policy_hash`` and the canonical policy JSON below are byte-identical
# between 0.1.3 and this branch, so existing ``SessionRecord`` bindings
# survive the S2 upgrade with zero migration. These goldens guard future
# drift: adding ANY AgentRoleSpec field (even with a default) or touching the
# ``compile_permission_policy`` serialization breaks all stored bindings — see
# the S2 solution doc §1.2 role_hash trap and §5 hard invariants.

_GOLDEN_013_ROLE_HASH = (
    "sha256:1dd9ea20a0e266d4d34f5102f88baed70748c015e679c41170976a4e9fabb752"
)
_GOLDEN_013_POLICY_HASH = (
    "sha256:5fd162e8c1fb9064d01f5721d1f181dc3d0a3e82129fa33b1a0234abcaae018d"
)
_GOLDEN_013_POLICY_JSON = (
    '{"autoApprove":["read","search"],'
    '"autoDeny":["delete","edit","execute","fetch","move","other","switch_mode"],'
    '"defaultAction":"deny"}'
)


def test_role_hash_stable_against_0_1_3_golden() -> None:
    role = load_role(VALID_ROLE)

    assert role_hash(role) == _GOLDEN_013_ROLE_HASH


def test_policy_hash_unchanged_by_prompt_flag_shape() -> None:
    # The S2 prompt-flag change must never alter the compiled policy identity
    # persisted in existing session records.
    role = load_role(VALID_ROLE)

    assert policy_hash(role) == _GOLDEN_013_POLICY_HASH
    canonical = json.dumps(
        compile_permission_policy(role), sort_keys=True, separators=(",", ":")
    )
    assert canonical == _GOLDEN_013_POLICY_JSON


def test_policy_hash_identical_for_exec_and_persistent_strategy() -> None:
    # policy_hash derives from permissions only; the session strategy flip and
    # the S2 session flag shape must not enter the policy identity.
    exec_role = load_role(VALID_ROLE)
    persistent = _persistent_role()

    assert policy_hash(exec_role) == policy_hash(persistent)


# --- native role-bound --mcp-config insertion --------------------------------
#
# Preflight-proven: acpx@0.12.0 accepts a global ``--mcp-config`` (exit 0) for
# exec and for sessions new/ensure/show, status, cancel, and close. The flag is
# inserted immediately after the acpx invocation prefix, before every other
# global flag, on ALL compiled commands, and it always carries the VERIFIED
# canonical config path — never the raw declared path. Compiling the declared
# path re-opens the symlink-swap TOCTOU: a symlink replaced after validation
# would make acpx read a different config than the recorded binding.


def _write_mcp_config(path: Path) -> None:
    path.write_text(json.dumps({"mcpServers": []}), encoding="utf-8")


def _mcp_exec_role(config: Path):
    spec = copy.deepcopy(VALID_ROLE)
    spec["runner"] = {**spec["runner"], "mcp_config": str(config)}
    return load_role(spec)


def _mcp_persistent_role(config: Path):
    spec = copy.deepcopy(VALID_ROLE)
    spec["session"] = {"strategy": "persistent"}
    spec["runner"] = {
        **spec["runner"],
        "acpx_binary": None,
        "mcp_config": str(config),
    }
    return load_role(spec)


def _all_session_commands(role) -> list[list[str]]:
    return [
        compile_session_create_command(role, cwd="/tmp/work", session_name="s"),
        compile_session_ensure_command(role, cwd="/tmp/work", session_name="s"),
        compile_session_show_command(role, cwd="/tmp/work", session_name="s"),
        compile_session_status_command(role, cwd="/tmp/work", session_name="s"),
        compile_session_cancel_command(role, cwd="/tmp/work", session_name="s"),
        compile_session_close_command(role, cwd="/tmp/work", session_name="s"),
        compile_session_prompt_command(
            role, cwd="/tmp/work", session_name="s", prompt="hi"
        ),
    ]


def test_compile_command_inserts_mcp_config_after_prefix_before_global_flags(
    tmp_path: Path,
) -> None:
    config = tmp_path / "mcp.json"
    _write_mcp_config(config)
    role = _mcp_exec_role(config)  # VALID_ROLE pins acpx_binary: 1-element prefix
    argv = compile_command(role, cwd="/tmp/work", prompt="hello")

    assert argv[0] == "/usr/bin/acpx"
    assert argv[1:3] == ["--mcp-config", os.path.realpath(config)]
    assert argv[3] == "--format"


def test_compile_command_omits_mcp_config_when_unset() -> None:
    role = load_role(VALID_ROLE)
    argv = compile_command(role, cwd="/tmp/work", prompt="hello")

    assert "--mcp-config" not in argv


def test_session_commands_insert_mcp_config_between_prefix_and_global_flags(
    tmp_path: Path,
) -> None:
    config = tmp_path / "mcp.json"
    _write_mcp_config(config)
    role = _mcp_persistent_role(config)

    for argv in _all_session_commands(role):
        assert argv[:3] == ["npx", "-y", "acpx@0.12.0"]
        assert argv[3:5] == ["--mcp-config", os.path.realpath(config)]
        assert argv[5] == "--format"


def test_session_commands_omit_mcp_config_when_unset() -> None:
    role = _persistent_role()

    for argv in _all_session_commands(role):
        assert "--mcp-config" not in argv


# --- symlink-swap TOCTOU regression: argv must carry the canonical path ------


def test_compile_command_uses_canonical_target_not_declared_symlink(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real-mcp.json"
    _write_mcp_config(real)
    link = tmp_path / "declared-mcp.json"
    link.symlink_to(real)
    role = _mcp_exec_role(link)

    argv = compile_command(role, cwd="/tmp/work", prompt="hello")

    # The compiled argv must consume the verified canonical target so a
    # post-validation symlink swap cannot redirect what acpx reads.
    assert argv[1:3] == ["--mcp-config", os.path.realpath(real)]
    assert str(link) not in argv


def test_all_session_commands_use_canonical_target_not_declared_symlink(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real-mcp.json"
    _write_mcp_config(real)
    link = tmp_path / "declared-mcp.json"
    link.symlink_to(real)
    role = _mcp_persistent_role(link)

    for argv in _all_session_commands(role):
        assert argv[3:5] == ["--mcp-config", os.path.realpath(real)]
        assert str(link) not in argv


def test_compile_command_consumes_supplied_verified_binding(tmp_path: Path) -> None:
    config = tmp_path / "mcp.json"
    _write_mcp_config(config)
    role = _mcp_exec_role(config)
    binding = resolve_mcp_config(role)
    # Deleting the file afterwards proves the compiler consumes the verified
    # binding it was handed instead of silently re-reading the declared path.
    config.unlink()

    argv = compile_command(role, cwd="/tmp/work", prompt="hello", mcp_binding=binding)

    assert argv[1:3] == ["--mcp-config", binding.path]


def test_compile_command_fails_closed_when_declared_config_unverifiable(
    tmp_path: Path,
) -> None:
    role = _mcp_exec_role(tmp_path / "absent-mcp.json")

    # Without a pre-verified binding the compiler must fail closed rather than
    # ever emitting an unverified declared path into argv.
    with pytest.raises(McpConfigError):
        compile_command(role, cwd="/tmp/work", prompt="hello")

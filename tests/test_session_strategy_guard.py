"""The Session-lifetime classification is deleted, not disabled.

A role used to declare ``session.strategy = exec | persistent``, and a pair of
guards refused to compile one shape for a role declared as the other. That was a
**Session-lifetime** classification: it asserted how long the Session behind a
role was supposed to live. A Session has no lifetime to declare — Runs
terminate, Sessions do not close — so the declaration and both guards are gone.

What survives is the only distinction that was ever real: an ``exec`` one-shot
argv and a ``prompt -s <name>`` turn argv are different **Run command shapes**.
A caller picks the shape it needs; nothing classifies a role and then decides
for it. These tests pin exactly that split, so the retirement cannot quietly
come back as a flag, a default, or an unused constant.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor import policy, role as role_module
from agent_run_supervisor.role import RoleValidationError, load_role

from tests.test_role import VALID_ROLE


def _role(work_dir: Path, session_block: Any = None) -> Any:
    spec = copy.deepcopy(VALID_ROLE)
    spec["workspace"] = dict(spec["workspace"])
    spec["workspace"]["default_cwd"] = str(work_dir)
    spec["workspace"]["allowed_roots"] = [str(work_dir)]
    if session_block is not None:
        spec["session"] = session_block
    else:
        spec.pop("session", None)
    return load_role(spec)


# -- the classification is gone ----------------------------------------------


def test_no_session_lifetime_guard_survives_in_policy() -> None:
    for retired in (
        "ensure_exec_strategy",
        "ensure_persistent_strategy",
        "ExecStrategyError",
        "compile_session_close_command",
    ):
        assert not hasattr(policy, retired), retired


def test_no_session_strategy_vocabulary_survives_in_role() -> None:
    assert not hasattr(role_module, "SESSION_STRATEGIES")
    assert "strategy" not in role_module.AgentSessionSpec.__dataclass_fields__


def test_a_role_declaring_a_session_strategy_is_refused_as_an_unknown_key(
    tmp_path: Path,
) -> None:
    """Deleted, not defaulted: the field is not silently accepted and ignored."""
    with pytest.raises(RoleValidationError) as err:
        _role(tmp_path, {"strategy": "persistent"})
    assert "strategy" in str(err.value)


# -- what a session block still carries --------------------------------------


def test_the_session_block_carries_only_a_lease_bound(tmp_path: Path) -> None:
    role = _role(tmp_path, {"lease_seconds": 120})
    assert role.session.lease_seconds == 120
    assert set(role_module.AgentSessionSpec.__dataclass_fields__) == {"lease_seconds"}


def test_an_absent_session_block_still_yields_a_finite_lease(tmp_path: Path) -> None:
    """Stale-lock recovery always has a bound to recover against."""
    role = _role(tmp_path)
    assert role.session.lease_seconds == role_module.DEFAULT_SESSION_LEASE_SECONDS


# -- the surviving distinction is a Run command shape ------------------------


def test_both_command_shapes_compile_from_the_same_role(tmp_path: Path) -> None:
    """One role, two Run command shapes, no classification in between."""
    role = _role(tmp_path)

    one_shot = policy.compile_command(role, str(tmp_path), "do the thing")
    turn = policy.compile_session_prompt_command(
        role, str(tmp_path), "sess-name", "do the thing"
    )

    assert one_shot[-3:] == [role.runner.adapter_agent, "exec", "do the thing"]
    assert turn[-5:] == [
        role.runner.adapter_agent,
        "prompt",
        "-s",
        "sess-name",
        "do the thing",
    ]


def test_management_shapes_compile_from_the_same_role(tmp_path: Path) -> None:
    role = _role(tmp_path)
    for compile_fn in (
        policy.compile_session_create_command,
        policy.compile_session_ensure_command,
        policy.compile_session_show_command,
        policy.compile_session_status_command,
        policy.compile_session_cancel_command,
    ):
        argv = compile_fn(role, str(tmp_path), "sess-name")
        assert argv[-1] == "sess-name"

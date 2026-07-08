"""S2 goal-turn composition tests.

``compose_goal_prompt`` owns the fail-closed composition of a goal-setting
slash prompt (e.g. Claude Code's ``/goal``) from caller-supplied goal text;
``is_slash_prompt`` is the detection signal recorded in turn artifacts. Both
protect the same seam: caller text must never silently change which slash
command (if any) the AGENT executes.
"""
from __future__ import annotations

import pytest

import copy

import agent_run_supervisor.goal as goal_module
from agent_run_supervisor.goal import (
    GOAL_SLASH_COMMAND,
    GOAL_TEMPLATE_VERSION,
    NATIVE_GOAL_ADAPTERS,
    CompiledGoalPrompt,
    GoalPromptError,
    GoalSpec,
    compile_goal_prompt,
    compose_goal_prompt,
    is_slash_prompt,
)
from agent_run_supervisor.role import load_role
from tests.test_role import VALID_ROLE


def _role_for_adapter(adapter_agent: str):
    spec = copy.deepcopy(VALID_ROLE)
    spec["runner"]["adapter_agent"] = adapter_agent
    return load_role(spec)


def test_goal_slash_command_constant() -> None:
    assert GOAL_SLASH_COMMAND == "/goal"


def test_compose_goal_prompt_prefixes_goal_command() -> None:
    assert compose_goal_prompt("ship the report") == "/goal ship the report"


def test_compose_goal_prompt_strips_surrounding_whitespace() -> None:
    assert compose_goal_prompt("  ship it \n") == "/goal ship it"


def test_compose_goal_prompt_preserves_multiline_goal_body() -> None:
    text = "finish A\n- test B\n- report C"

    assert compose_goal_prompt(text) == f"/goal {text}"


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_compose_goal_prompt_rejects_empty_goal_text(bad: str) -> None:
    with pytest.raises(GoalPromptError):
        compose_goal_prompt(bad)


def test_compose_goal_prompt_rejects_nested_slash_command() -> None:
    # Goal text that itself starts with "/" would shadow or replace the
    # intended /goal command; fail closed instead of composing it.
    with pytest.raises(GoalPromptError):
        compose_goal_prompt("/clear")
    with pytest.raises(GoalPromptError):
        compose_goal_prompt("  /goal recursive")


def test_compose_goal_prompt_rejects_control_characters() -> None:
    with pytest.raises(GoalPromptError):
        compose_goal_prompt("ship\x00it")
    with pytest.raises(GoalPromptError):
        compose_goal_prompt("ship\x1b[31mit")
    with pytest.raises(GoalPromptError):
        compose_goal_prompt("ship\x7fit")


def test_compose_goal_prompt_allows_tabs_and_newlines_inside_body() -> None:
    assert compose_goal_prompt("a\tb\nc") == "/goal a\tb\nc"


def test_compose_goal_prompt_rejects_non_string() -> None:
    with pytest.raises(GoalPromptError):
        compose_goal_prompt(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("/goal ship it", True),
        ("/goal", True),
        ("/goal\nmultiline body", True),
        ("/compact keep the summary", True),
        ("/re-view:pr 42", True),
        ("hello", False),
        (" /goal not at start", False),
        ("/", False),
        ("//weird", False),
        ("", False),
    ],
)
def test_is_slash_prompt(prompt: str, expected: bool) -> None:
    assert is_slash_prompt(prompt) is expected


# --- S2 goal-contract compilation (solution §2.2 policy layer) ---------------
#
# Neither Claude Code nor Codex exposes a native ACP `/goal` command (the S1a
# fixtures prove codex's advertised command list), so a literal slash turn is
# a guaranteed no-op there. `compile_goal_prompt` therefore renders the
# versioned plain-text goal contract for every adapter not explicitly
# registered as native; the registry starts empty and requires fixture proof
# to grow.


def test_native_goal_adapters_registry_starts_empty() -> None:
    assert NATIVE_GOAL_ADAPTERS == frozenset()


@pytest.mark.parametrize("adapter", ["claude", "codex"])
def test_compile_goal_prompt_renders_text_template_for_known_adapters(
    adapter: str,
) -> None:
    role = _role_for_adapter(adapter)
    goal = GoalSpec(
        goal_text="ship the S2 report",
        acceptance_criteria=("tests green", "docs synced"),
    )

    compiled = compile_goal_prompt(role, goal)

    assert isinstance(compiled, CompiledGoalPrompt)
    assert compiled.prompt_kind == "text"
    assert compiled.template_version == GOAL_TEMPLATE_VERSION == "goal-contract/v1"
    # Never a slash command for non-native adapters.
    assert not compiled.prompt.startswith("/")
    assert "ship the S2 report" in compiled.prompt
    assert "- tests green" in compiled.prompt
    assert "- docs synced" in compiled.prompt


def test_compile_goal_prompt_template_carries_status_anchor_contract() -> None:
    compiled = compile_goal_prompt(
        _role_for_adapter("claude"), GoalSpec(goal_text="ship it")
    )

    assert "GOAL_STATUS: DONE" in compiled.prompt
    assert "GOAL_STATUS: CONTINUE" in compiled.prompt
    # The version marker is embedded so judges can pin template evolution.
    assert "goal-contract/v1" in compiled.prompt


def test_compile_goal_prompt_omits_criteria_block_when_empty() -> None:
    compiled = compile_goal_prompt(
        _role_for_adapter("claude"), GoalSpec(goal_text="ship it")
    )

    assert "Acceptance criteria" not in compiled.prompt


def test_compile_goal_prompt_native_adapter_passes_through_slash(monkeypatch) -> None:
    monkeypatch.setattr(goal_module, "NATIVE_GOAL_ADAPTERS", frozenset({"claude"}))

    compiled = compile_goal_prompt(
        _role_for_adapter("claude"), GoalSpec(goal_text="ship it")
    )

    assert compiled.prompt == "/goal ship it"
    assert compiled.prompt_kind == "slash_command"
    assert compiled.template_version is None


def test_compile_goal_prompt_applies_compose_validation_rules() -> None:
    role = _role_for_adapter("claude")
    with pytest.raises(GoalPromptError):
        compile_goal_prompt(role, GoalSpec(goal_text="   "))
    with pytest.raises(GoalPromptError):
        compile_goal_prompt(role, GoalSpec(goal_text="/clear"))
    with pytest.raises(GoalPromptError):
        compile_goal_prompt(role, GoalSpec(goal_text="ship\x00it"))


def test_compile_goal_prompt_rejects_bad_acceptance_criteria() -> None:
    role = _role_for_adapter("claude")
    with pytest.raises(GoalPromptError):
        compile_goal_prompt(
            role, GoalSpec(goal_text="ship it", acceptance_criteria=("",))
        )
    with pytest.raises(GoalPromptError):
        compile_goal_prompt(
            role,
            GoalSpec(goal_text="ship it", acceptance_criteria=("a\nmultiline",)),
        )

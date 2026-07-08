"""S2 goal-turn composition tests.

``compose_goal_prompt`` owns the fail-closed composition of a goal-setting
slash prompt (e.g. Claude Code's ``/goal``) from caller-supplied goal text;
``is_slash_prompt`` is the detection signal recorded in turn artifacts. Both
protect the same seam: caller text must never silently change which slash
command (if any) the AGENT executes.
"""
from __future__ import annotations

import pytest

from agent_run_supervisor.goal import (
    GOAL_SLASH_COMMAND,
    GoalPromptError,
    compose_goal_prompt,
    is_slash_prompt,
)


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

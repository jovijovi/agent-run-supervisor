"""Safe composition of goal-setting slash prompts for persistent sessions.

A persistent AGENT session accepts slash commands (e.g. Claude Code's
``/goal``) as ordinary prompt text. Composing that text from caller-supplied
goal content is an injection seam: goal text that itself starts with ``/``
would shadow or replace the intended command, and control characters can
smuggle arbitrary framing into the turn. This module owns the fail-closed
composition rules. The composed prompt still travels as a single argv element
through the command compilers — never a shell string.
"""
from __future__ import annotations

import re

GOAL_SLASH_COMMAND = "/goal"

# C0 control characters other than tab/newline (plus DEL) never belong in goal
# text; carriage returns are also refused so composed prompts stay LF-framed.
_FORBIDDEN_CONTROL = frozenset(
    chr(code) for code in range(0x00, 0x20) if chr(code) not in ("\t", "\n")
) | {"\x7f"}

# A prompt the AGENT would interpret as a slash command: "/" followed by an
# alphanumeric command name (word/colon/dash characters), then end-of-prompt
# or whitespace before any arguments.
_SLASH_PROMPT = re.compile(r"^/[A-Za-z0-9][A-Za-z0-9_:-]*(\s|$)")


class GoalPromptError(ValueError):
    """Raised when caller-supplied goal text cannot be composed safely."""


def compose_goal_prompt(goal_text: str) -> str:
    """Compose a validated ``/goal <text>`` prompt from caller goal text.

    Fails closed on non-string input, empty/whitespace-only text, text that
    starts with ``/`` (it would nest another slash command), and forbidden
    control characters. Surrounding whitespace is stripped; interior newlines
    and tabs are preserved so multi-line goals stay intact.
    """
    if not isinstance(goal_text, str):
        raise GoalPromptError("goal text must be a string")
    text = goal_text.strip()
    if not text:
        raise GoalPromptError("goal text must not be empty")
    if text.startswith("/"):
        raise GoalPromptError(
            "goal text must not start with '/': it would nest another slash command"
        )
    forbidden = sorted({f"0x{ord(ch):02x}" for ch in text if ch in _FORBIDDEN_CONTROL})
    if forbidden:
        raise GoalPromptError(
            f"goal text contains forbidden control characters: {', '.join(forbidden)}"
        )
    return f"{GOAL_SLASH_COMMAND} {text}"


def is_slash_prompt(prompt: str) -> bool:
    """Whether the AGENT would interpret ``prompt`` as a slash command."""
    return isinstance(prompt, str) and bool(_SLASH_PROMPT.match(prompt))

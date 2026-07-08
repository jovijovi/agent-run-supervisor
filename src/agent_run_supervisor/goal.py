"""Goal-turn semantics: safe slash composition and adapter-aware compilation.

Two layers live here:

- **Mechanism** — ``compose_goal_prompt`` builds a literal ``/goal <text>``
  slash prompt with fail-closed validation (goal text starting with ``/``
  would shadow another slash command; control characters can smuggle framing).
  The composed prompt always travels as a single argv element — never a shell
  string.
- **Policy** — ``compile_goal_prompt`` maps a normalized :class:`GoalSpec`
  onto what the role's adapter can actually execute. Neither Claude Code nor
  Codex exposes a native ACP ``goal`` command (the S1a fixtures capture
  codex's advertised command list), so a literal slash turn is a guaranteed
  no-op there; non-native adapters get the versioned plain-text goal contract
  instead. The contract's trailing ``GOAL_STATUS:`` line is a deterministic
  anchor for the caller's judge loop — this library never interprets it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from agent_run_supervisor.role import AgentRoleSpec

GOAL_SLASH_COMMAND = "/goal"

GOAL_TEMPLATE_VERSION = "goal-contract/v1"

# Adapters whose ACP surface natively executes a `/goal` slash command.
# Deliberately empty: registering an adapter requires a fixture-proven
# command-list capture showing native support (S2 solution doc §2.2).
NATIVE_GOAL_ADAPTERS: frozenset[str] = frozenset()

PROMPT_KIND_TEXT = "text"
PROMPT_KIND_SLASH_COMMAND = "slash_command"

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
    """Raised when caller-supplied goal content cannot be composed safely."""


@dataclass(frozen=True)
class GoalSpec:
    """Normalized, adapter-independent goal semantics for one compiled turn."""

    goal_text: str
    acceptance_criteria: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledGoalPrompt:
    """One adapter-ready goal turn: prompt text plus how the AGENT reads it."""

    prompt: str
    prompt_kind: str  # PROMPT_KIND_TEXT or PROMPT_KIND_SLASH_COMMAND
    template_version: str | None


def _validated_goal_text(goal_text: str) -> str:
    """Shared fail-closed validation for caller goal text (stripped)."""
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
    return text


def compose_goal_prompt(goal_text: str) -> str:
    """Compose a validated ``/goal <text>`` prompt from caller goal text.

    Fails closed on non-string input, empty/whitespace-only text, text that
    starts with ``/`` (it would nest another slash command), and forbidden
    control characters. Surrounding whitespace is stripped; interior newlines
    and tabs are preserved so multi-line goals stay intact.
    """
    return f"{GOAL_SLASH_COMMAND} {_validated_goal_text(goal_text)}"


def _validated_criteria(criteria: tuple[str, ...]) -> tuple[str, ...]:
    validated: list[str] = []
    for item in criteria:
        if not isinstance(item, str) or not item.strip():
            raise GoalPromptError(
                "acceptance criteria entries must be non-empty strings"
            )
        if "\n" in item or "\r" in item:
            raise GoalPromptError(
                "acceptance criteria entries must be single-line strings"
            )
        validated.append(item.strip())
    return tuple(validated)


def compile_goal_prompt(role: AgentRoleSpec, goal: GoalSpec) -> CompiledGoalPrompt:
    """Compile a :class:`GoalSpec` into one adapter-appropriate goal turn.

    Adapters registered in :data:`NATIVE_GOAL_ADAPTERS` get the literal
    ``/goal`` slash prompt (still covered by the runtime's no-op fail-closed
    classification). Every other adapter gets the ``goal-contract/v1``
    plain-text template: goal body, optional acceptance criteria, keep-working
    constraints, and the deterministic trailing ``GOAL_STATUS:`` anchor the
    caller's judge loop keys on. Template wording evolves only with the
    version marker so judge parsers can pin it.
    """
    text = _validated_goal_text(goal.goal_text)
    criteria = _validated_criteria(goal.acceptance_criteria)

    if role.runner.adapter_agent in NATIVE_GOAL_ADAPTERS:
        return CompiledGoalPrompt(
            prompt=compose_goal_prompt(text),
            prompt_kind=PROMPT_KIND_SLASH_COMMAND,
            template_version=None,
        )

    lines = [
        f"[{GOAL_TEMPLATE_VERSION}] Standing goal for this supervised run:",
        "",
        text,
        "",
    ]
    if criteria:
        lines.append("Acceptance criteria:")
        lines.extend(f"- {item}" for item in criteria)
        lines.append("")
    lines.extend(
        [
            "Constraints: keep working toward this goal within this supervised "
            "run until it is complete or you are genuinely blocked; do not stop "
            "to ask questions or wait for further input.",
            "",
            "When you stop, end your reply with exactly one final line:",
            "GOAL_STATUS: DONE",
            "or",
            "GOAL_STATUS: CONTINUE <one-line reason>",
        ]
    )
    return CompiledGoalPrompt(
        prompt="\n".join(lines),
        prompt_kind=PROMPT_KIND_TEXT,
        template_version=GOAL_TEMPLATE_VERSION,
    )


def is_slash_prompt(prompt: str) -> bool:
    """Whether the AGENT would interpret ``prompt`` as a slash command."""
    return isinstance(prompt, str) and bool(_SLASH_PROMPT.match(prompt))

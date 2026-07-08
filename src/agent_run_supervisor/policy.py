from __future__ import annotations

import hashlib
import json

from agent_run_supervisor.role import AgentRoleSpec


class ExecStrategyError(ValueError):
    """Raised when exec compilation/launch is attempted for a non-exec role.

    Once a role declares ``session.strategy='persistent'`` the one-shot exec
    path must fail closed instead of silently launching ``acpx exec``. Persistent
    roles use the separate session runtime/CLI surface instead.
    """


def ensure_exec_strategy(role: AgentRoleSpec) -> None:
    """Fail closed unless the role is an exec-strategy role."""
    strategy = role.session.strategy
    if strategy != "exec":
        raise ExecStrategyError(
            f"role {role.role_id!r} uses session.strategy={strategy!r}; exec "
            "compilation/launch is refused for non-exec strategies "
            "(use the session runtime/CLI for persistent roles)."
        )


def ensure_persistent_strategy(role: AgentRoleSpec) -> None:
    """Fail closed unless the role is a persistent-session role.

    The mirror of :func:`ensure_exec_strategy`: an exec-strategy role must never
    reach the persistent-session command compilers or runtime, just as a
    persistent role must never launch a one-shot ``acpx exec``.
    """
    strategy = role.session.strategy
    if strategy != "persistent":
        raise ExecStrategyError(
            f"role {role.role_id!r} uses session.strategy={strategy!r}; "
            "persistent-session compilation/launch is refused for "
            "non-persistent strategies."
        )


ACPX_PERMISSION_KIND_FOR_ROLE: dict[str, str] = {
    "read": "read",
    "search": "search",
    "write": "edit",
    "execute": "execute",
    "delete": "delete",
    "move": "move",
    "fetch": "fetch",
    "switch_mode": "switch_mode",
    "other": "other",
}


def compile_permission_policy(role: AgentRoleSpec) -> dict:
    auto_approve: list[str] = []
    auto_deny: list[str] = []
    perms = role.permissions
    for role_kind, acpx_kind in ACPX_PERMISSION_KIND_FOR_ROLE.items():
        if getattr(perms, role_kind):
            auto_approve.append(acpx_kind)
        else:
            auto_deny.append(acpx_kind)
    return {
        "defaultAction": "deny",
        "autoApprove": sorted(auto_approve),
        "autoDeny": sorted(auto_deny),
    }


def _acpx_prefix(role: AgentRoleSpec) -> list[str]:
    """Pinned acpx invocation prefix: explicit binary, else pinned ``npx`` fetch."""
    if role.runner.acpx_binary:
        return [role.runner.acpx_binary]
    return ["npx", "-y", f"acpx@{role.runner.acpx_version}"]


def _resolve_cwd(role: AgentRoleSpec, cwd: str | None) -> str:
    return cwd if cwd else role.workspace.default_cwd


def _permission_policy_json(role: AgentRoleSpec) -> str:
    """Canonical JSON for the role-derived acpx ``--permission-policy`` value."""
    return json.dumps(
        compile_permission_policy(role), sort_keys=True, separators=(",", ":")
    )


def _exec_turn_flags(role: AgentRoleSpec, resolved_cwd: str) -> list[str]:
    """Authorization/automation flag block for one-shot exec runs."""
    policy_json = _permission_policy_json(role)
    flags = [
        "--format",
        "json",
        "--json-strict",
        "--suppress-reads",
        "--timeout",
        str(role.limits.timeout_seconds),
        "--max-turns",
        str(role.limits.max_turns),
        "--cwd",
        resolved_cwd,
        "--permission-policy",
        policy_json,
        "--non-interactive-permissions",
        "fail",
    ]
    if not role.permissions.terminal:
        flags.append("--no-terminal")
    if role.runner.model:
        flags.extend(["--model", role.runner.model])
    return flags


def _session_prompt_flags(role: AgentRoleSpec, resolved_cwd: str) -> list[str]:
    """Authorization/automation flag block for session prompt turns.

    Since S2, prompt-turn permissions are role-derived: a role granting at
    least one acpx permission kind compiles the same ``--permission-policy``
    JSON as the exec path (the flag lives in the shared global flag family;
    ``fixtures/acpx-0.12.0/permission-policy-deny-all-sentinel`` proves acpx
    accepts it there). A role granting **no** kinds keeps the stricter S1a
    fixture-proven ``--deny-all`` shape, so all-deny roles stay byte-identical
    to the proven fail-closed command. Both shapes keep
    ``--non-interactive-permissions fail`` so an unexpected permission prompt
    fails the turn instead of hanging it.
    """
    flags = [
        "--format",
        "json",
        "--json-strict",
        "--suppress-reads",
        "--timeout",
        str(role.limits.timeout_seconds),
        "--max-turns",
        str(role.limits.max_turns),
        "--cwd",
        resolved_cwd,
    ]
    policy = compile_permission_policy(role)
    if policy["autoApprove"]:
        flags.extend(["--permission-policy", _permission_policy_json(role)])
    else:
        flags.append("--deny-all")
    flags.extend(["--non-interactive-permissions", "fail"])
    if not role.permissions.terminal:
        flags.append("--no-terminal")
    if role.runner.model:
        flags.extend(["--model", role.runner.model])
    return flags


def _management_flags(resolved_cwd: str) -> list[str]:
    """Flag block for session *management* commands (no AGENT prompt turn).

    Fixture-proven: management commands (``sessions new/ensure/show``,
    ``status``) carry only output/cwd flags — never the turn authorization
    block — because they query/mutate local session bookkeeping rather than
    running an AGENT under policy.
    """
    return ["--format", "json", "--json-strict", "--cwd", resolved_cwd]


def _require_session_name(session_name: str) -> str:
    if not isinstance(session_name, str) or not session_name:
        raise ValueError("session_name must be a non-empty string")
    return session_name


def compile_command(
    role: AgentRoleSpec,
    cwd: str | None,
    prompt: str,
) -> list[str]:
    ensure_exec_strategy(role)
    argv = _acpx_prefix(role)
    argv.extend(_exec_turn_flags(role, _resolve_cwd(role, cwd)))
    argv.extend([role.runner.adapter_agent, "exec", prompt])
    return argv


def compile_session_create_command(
    role: AgentRoleSpec,
    cwd: str | None,
    session_name: str,
) -> list[str]:
    """``<adapter> sessions new --name <session_name>`` (creates a named session)."""
    ensure_persistent_strategy(role)
    name = _require_session_name(session_name)
    argv = _acpx_prefix(role)
    argv.extend(_management_flags(_resolve_cwd(role, cwd)))
    argv.extend([role.runner.adapter_agent, "sessions", "new", "--name", name])
    return argv


def compile_session_ensure_command(
    role: AgentRoleSpec,
    cwd: str | None,
    session_name: str,
) -> list[str]:
    """``<adapter> sessions ensure --name <session_name>`` (idempotent open)."""
    ensure_persistent_strategy(role)
    name = _require_session_name(session_name)
    argv = _acpx_prefix(role)
    argv.extend(_management_flags(_resolve_cwd(role, cwd)))
    argv.extend([role.runner.adapter_agent, "sessions", "ensure", "--name", name])
    return argv


def compile_session_show_command(
    role: AgentRoleSpec,
    cwd: str | None,
    session_name: str,
) -> list[str]:
    """``<adapter> sessions show <session_name>`` (durable session record query)."""
    ensure_persistent_strategy(role)
    name = _require_session_name(session_name)
    argv = _acpx_prefix(role)
    argv.extend(_management_flags(_resolve_cwd(role, cwd)))
    argv.extend([role.runner.adapter_agent, "sessions", "show", name])
    return argv


def compile_session_status_command(
    role: AgentRoleSpec,
    cwd: str | None,
    session_name: str,
) -> list[str]:
    """``<adapter> status -s <session_name>`` (live status snapshot query)."""
    ensure_persistent_strategy(role)
    name = _require_session_name(session_name)
    argv = _acpx_prefix(role)
    argv.extend(_management_flags(_resolve_cwd(role, cwd)))
    argv.extend([role.runner.adapter_agent, "status", "-s", name])
    return argv


def compile_session_close_command(
    role: AgentRoleSpec,
    cwd: str | None,
    session_name: str,
) -> list[str]:
    """``<adapter> sessions close <session_name>`` (terminal session close).

    Fixture-proven by ``fixtures/acpx-0.12.0/session-close-named``. A management
    command: it carries only the output/cwd block, never an exec/turn
    authorization block, and the session name is always a single argv element.
    """
    ensure_persistent_strategy(role)
    name = _require_session_name(session_name)
    argv = _acpx_prefix(role)
    argv.extend(_management_flags(_resolve_cwd(role, cwd)))
    argv.extend([role.runner.adapter_agent, "sessions", "close", name])
    return argv


def compile_session_cancel_command(
    role: AgentRoleSpec,
    cwd: str | None,
    session_name: str,
) -> list[str]:
    """``<adapter> cancel -s <session_name>`` (cooperative cancel of active work).

    Fixture-proven by ``fixtures/acpx-0.12.0/session-cancel-no-active``. Note the
    grammar is ``cancel -s <name>`` (not ``sessions cancel``). A management
    command: output/cwd block only, no exec/turn authorization, single argv name.
    """
    ensure_persistent_strategy(role)
    name = _require_session_name(session_name)
    argv = _acpx_prefix(role)
    argv.extend(_management_flags(_resolve_cwd(role, cwd)))
    argv.extend([role.runner.adapter_agent, "cancel", "-s", name])
    return argv


def compile_session_prompt_command(
    role: AgentRoleSpec,
    cwd: str | None,
    session_name: str,
    prompt: str,
) -> list[str]:
    """``<adapter> prompt -s <session_name> <prompt>`` (one role-bound turn).

    Carries the role-derived turn authorization block (see
    :func:`_session_prompt_flags`): granted permission kinds compile to
    ``--permission-policy``; an all-deny role keeps the S1a fixture-proven
    ``--deny-all`` shape. The local session record remains role/policy-bound
    and is revalidated before each turn. The prompt is always a single argv
    element; the compiler never uses a shell.
    """
    ensure_persistent_strategy(role)
    name = _require_session_name(session_name)
    argv = _acpx_prefix(role)
    argv.extend(_session_prompt_flags(role, _resolve_cwd(role, cwd)))
    argv.extend([role.runner.adapter_agent, "prompt", "-s", name, prompt])
    return argv


def policy_hash(role: AgentRoleSpec) -> str:
    policy = compile_permission_policy(role)
    payload = json.dumps(policy, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

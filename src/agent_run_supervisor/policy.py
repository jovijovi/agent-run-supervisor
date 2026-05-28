from __future__ import annotations

import hashlib
import json

from agent_run_supervisor.role import AgentRoleSpec

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


def compile_command(
    role: AgentRoleSpec,
    cwd: str | None,
    prompt: str,
) -> list[str]:
    if role.runner.acpx_binary:
        argv: list[str] = [role.runner.acpx_binary]
    else:
        argv = ["npx", "-y", f"acpx@{role.runner.acpx_version}"]

    resolved_cwd = cwd if cwd else role.workspace.default_cwd
    policy = compile_permission_policy(role)
    policy_json = json.dumps(policy, sort_keys=True, separators=(",", ":"))

    argv.extend(
        [
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
    )
    if not role.permissions.terminal:
        argv.append("--no-terminal")
    if role.runner.model:
        argv.extend(["--model", role.runner.model])
    argv.extend([role.runner.adapter_agent, "exec", prompt])
    return argv


def policy_hash(role: AgentRoleSpec) -> str:
    policy = compile_permission_policy(role)
    payload = json.dumps(policy, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

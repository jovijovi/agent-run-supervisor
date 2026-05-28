"""Cwd-vs-allowed-roots gate.

This is **cwd/config validation only** — not an OS or filesystem sandbox.
The role's `allowed_roots_security_boundary` flag must remain `False` in
V0.1b; a future phase would need explicit approval before any sandbox
claim could be made.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_run_supervisor.role import AgentRoleSpec

ALLOWED_ROOTS_DISCLAIMER = (
    "allowed_roots is cwd/config validation only — not an OS/filesystem sandbox "
    "and not a security boundary."
)


class WorkspaceValidationError(ValueError):
    """Raised when the effective cwd is not inside any configured allowed root."""


@dataclass(frozen=True)
class WorkspaceValidationResult:
    ok: bool
    effective_cwd: Path
    matched_root: Path | None
    allowed_roots_security_boundary: bool
    disclaimer: str


def resolve_effective_cwd(role: AgentRoleSpec, override: str | None) -> Path:
    raw = override if override is not None else role.workspace.default_cwd
    return Path(raw).expanduser().resolve()


def validate_effective_cwd(
    role: AgentRoleSpec,
    override: str | None,
) -> WorkspaceValidationResult:
    effective = resolve_effective_cwd(role, override)
    roots = [Path(root).expanduser().resolve() for root in role.workspace.allowed_roots]

    matched_root: Path | None = None
    for root in roots:
        if _is_within(effective, root):
            matched_root = root
            break

    if matched_root is None:
        which = "override --cwd" if override is not None else "default_cwd"
        rendered_roots = ", ".join(str(r) for r in roots) or "<none>"
        raise WorkspaceValidationError(
            f"{which} {effective} is not inside any configured allowed_roots "
            f"({rendered_roots}). Note: allowed_roots is config validation only — "
            "not an OS/filesystem sandbox and not a security boundary."
        )

    return WorkspaceValidationResult(
        ok=True,
        effective_cwd=effective,
        matched_root=matched_root,
        allowed_roots_security_boundary=False,
        disclaimer=ALLOWED_ROOTS_DISCLAIMER,
    )


def _is_within(candidate: Path, root: Path) -> bool:
    if candidate == root:
        return True
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False

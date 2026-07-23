"""Default-deny permission mediation over the frozen execution grant (R7).

This is cooperative-agent policy enforcement, not an OS sandbox: the bridge
maps registered ACP permission/filesystem request classes to deterministic
allow/deny decisions, records every decision as redacted mediation evidence,
and never widens or re-reads the grant at runtime (snapshot only). Unknown
operation classes deny by default; an unmappable/unexpected permission
prompt additionally flags the turn as failed, aligning with the acpx
non-interactive fail semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

# Registered read-like ACP tool kinds that the first-E2E read-only grant may
# allow inside the bound workspace. Every other registered kind — and any
# unregistered kind — denies.
_READ_KINDS = frozenset({"read", "search"})
_REGISTERED_KINDS = frozenset(
    {
        "read",
        "edit",
        "delete",
        "move",
        "search",
        "execute",
        "think",
        "fetch",
        "switch_mode",
        "other",
    }
)

# Write-family ACP tool kinds and the grant capability each one requires. A
# write-family tool call that reaches ``completed`` without that capability in
# the frozen grant is a grant violation: the agent performed a side effect the
# grant never allowed and no mediation could have legitimately approved. This
# is the honest fail-closed backstop behind the client-mediated ask/deny
# launch binding — detection, never prevention (the tool already ran).
_WRITE_FAMILY_REQUIRED_CAPABILITY: dict[str, str] = {
    "edit": "write",
    "delete": "delete",
    "move": "move",
    "execute": "execute",
}


@dataclass(frozen=True)
class MediationEvent:
    """Structural mediation evidence: operation family, decision, reason."""

    requested_op: str
    decision: str
    reason: str
    tool_call_id: str | None = None

    def to_event(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "permission_mediation",
            "requested_op": self.requested_op,
            "decision": self.decision,
            "reason": self.reason,
        }
        if self.tool_call_id is not None:
            payload["tool_call_id"] = self.tool_call_id
        return payload


class PermissionBridge:
    """Frozen-grant → deterministic mediation decisions + evidence."""

    def __init__(
        self,
        *,
        capabilities: Any,
        workspace_root: Path,
        evidence_sink: Callable[[MediationEvent], None],
    ) -> None:
        # Grant snapshot only: the capability set is frozen at construction
        # and never re-read, so runtime widening is invisible.
        self._capabilities = frozenset(capabilities)
        self._workspace_root = Path(workspace_root).resolve()
        self._evidence_sink = evidence_sink
        self.turn_failed = False
        self.turn_failure_reason: str | None = None
        # A4-S2 backstop state: a write-family tool completed without the
        # grant capability that could ever have allowed it.
        self.grant_violation = False
        self.grant_violation_reason: str | None = None
        self._tool_kinds: dict[str, str] = {}

    # -- initialize-time declaration ---------------------------------------

    def client_capabilities(self) -> dict[str, Any]:
        """clientCapabilities built from the frozen grant: fs read only when
        granted, write refused, terminal not provided."""
        return {
            "fs": {
                "readTextFile": "read" in self._capabilities,
                "writeTextFile": False,
            },
            "terminal": False,
        }

    # -- helpers -----------------------------------------------------------

    def _emit(
        self,
        *,
        requested_op: str,
        decision: str,
        reason: str,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        self._evidence_sink(
            MediationEvent(
                requested_op=requested_op,
                decision=decision,
                reason=reason,
                tool_call_id=tool_call_id,
            )
        )
        result: dict[str, Any] = {"decision": decision, "reason": reason}
        return result

    def _resolve_workspace_path(self, path: str) -> Path:
        """The single canonical workspace-bound resolution: a relative path
        is workspace-root-relative — never supervisor-process-cwd-relative —
        and symlinks are resolved before the containment decision."""
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._workspace_root / candidate
        return candidate.resolve()

    def _inside_workspace(self, resolved: Path) -> bool:
        return resolved == self._workspace_root or (
            self._workspace_root in resolved.parents
        )

    # -- filesystem mediation ----------------------------------------------

    def decide_fs_read(self, path: str) -> dict[str, Any]:
        if "read" not in self._capabilities:
            return self._emit(
                requested_op="fs_read",
                decision="deny",
                reason="grant does not include read",
            )
        resolved = self._resolve_workspace_path(path)
        if not self._inside_workspace(resolved):
            return self._emit(
                requested_op="fs_read",
                decision="deny",
                reason="path is outside the bound workspace",
            )
        decision = self._emit(
            requested_op="fs_read",
            decision="allow",
            reason="workspace-internal read under read grant",
        )
        # The actual read must use exactly the path the decision validated.
        decision["resolved_path"] = str(resolved)
        return decision

    def decide_fs_write(self, path: str) -> dict[str, Any]:
        return self._emit(
            requested_op="fs_write",
            decision="deny",
            reason="write is not permitted by the frozen grant",
        )

    # -- ACP permission-request mediation ------------------------------------

    def decide_permission_request(
        self, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Map one session/request_permission prompt to a deterministic
        decision dict for the SDK client (allow needs an option id)."""
        tool_call = request.get("tool_call") or {}
        tool_call_id = tool_call.get("toolCallId")
        kind = tool_call.get("kind")
        options = request.get("options") or []

        if not isinstance(kind, str) or not kind:
            # Unexpected/unmappable prompt: deny and fail the turn, aligning
            # with the acpx non-interactive fail semantics.
            self.turn_failed = True
            self.turn_failure_reason = "unmappable permission request"
            return self._deny_with_option(
                requested_op="permission:unknown",
                reason="unmappable permission request (no registered kind)",
                tool_call_id=tool_call_id,
                options=options,
            )

        if kind not in _REGISTERED_KINDS:
            return self._deny_with_option(
                requested_op=f"permission:{kind}",
                reason=f"unregistered request type {kind!r} denies by default",
                tool_call_id=tool_call_id,
                options=options,
            )

        if kind in _READ_KINDS and "read" in self._capabilities:
            allow_option = _option_id(options, {"allow_once", "allow_always"})
            if allow_option is not None:
                decision = self._emit(
                    requested_op=f"permission:{kind}",
                    decision="allow",
                    reason="workspace-scoped read-like operation under read grant",
                    tool_call_id=tool_call_id,
                )
                decision["option_id"] = allow_option
                return decision
            return self._deny_with_option(
                requested_op=f"permission:{kind}",
                reason="no allow option offered; denying",
                tool_call_id=tool_call_id,
                options=options,
            )

        return self._deny_with_option(
            requested_op=f"permission:{kind}",
            reason=f"{kind!r} is not permitted by the frozen grant",
            tool_call_id=tool_call_id,
            options=options,
        )

    # -- write-family completion backstop ------------------------------------

    def observe_tool_update(
        self, update: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Watch raw session updates for write-family tool completions the
        frozen grant never allowed (A4-S2 backstop).

        Kind is correlated by ``toolCallId`` because completed updates may
        omit it (real OpenCode does). Returns a durable ``permission_violation``
        event dict on detection, else ``None``. A completion whose kind was
        never observed cannot be proven write-family and does not flag — the
        mediated ask/deny launch binding is the prevention layer.
        """
        update_type = update.get("sessionUpdate")
        if update_type not in ("tool_call", "tool_call_update"):
            return None
        tool_call_id = update.get("toolCallId")
        kind = update.get("kind")
        if isinstance(tool_call_id, str) and isinstance(kind, str):
            self._tool_kinds[tool_call_id] = kind
        if update.get("status") != "completed":
            return None
        if not isinstance(kind, str) and isinstance(tool_call_id, str):
            kind = self._tool_kinds.get(tool_call_id)
        required = (
            _WRITE_FAMILY_REQUIRED_CAPABILITY.get(kind)
            if isinstance(kind, str)
            else None
        )
        if required is None or required in self._capabilities:
            return None
        self.grant_violation = True
        reason = (
            f"write-family tool kind {kind!r} completed without the required "
            f"{required!r} capability in the frozen grant"
        )
        self.grant_violation_reason = reason
        return {
            "type": "permission_violation",
            "tool_call_id": tool_call_id if isinstance(tool_call_id, str) else None,
            "kind": kind,
            "required_capability": required,
            "reason": reason,
        }

    def _deny_with_option(
        self,
        *,
        requested_op: str,
        reason: str,
        tool_call_id: str | None,
        options: Any,
    ) -> dict[str, Any]:
        decision = self._emit(
            requested_op=requested_op,
            decision="deny",
            reason=reason,
            tool_call_id=tool_call_id,
        )
        reject_option = _option_id(options, {"reject_once", "reject_always"})
        if reject_option is not None:
            decision["option_id"] = reject_option
        return decision


def _option_id(options: Any, kinds: frozenset[str] | set[str]) -> str | None:
    for option in options or []:
        if isinstance(option, Mapping) and option.get("kind") in kinds:
            option_id = option.get("optionId")
            if isinstance(option_id, str):
                return option_id
    return None

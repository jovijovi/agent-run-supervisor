"""C7: default-deny permission bridge over the frozen execution grant."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_run_supervisor.native_acp.permissions import (
    MediationEvent,
    PermissionBridge,
)

ALLOW_OPTION = {"optionId": "opt-allow", "name": "Allow", "kind": "allow_once"}
REJECT_OPTION = {"optionId": "opt-reject", "name": "Reject", "kind": "reject_once"}


def _bridge(tmp_path: Path, capabilities=("read",), events=None):
    events = events if events is not None else []
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    bridge = PermissionBridge(
        capabilities=capabilities,
        workspace_root=workspace,
        evidence_sink=events.append,
    )
    return bridge, workspace, events


def _request(kind: str | None, *, options=None):
    tool_call = {"toolCallId": "tool-1", "status": "pending"}
    if kind is not None:
        tool_call["kind"] = kind
    return {
        "session_id": "external-1",
        "tool_call": tool_call,
        "options": list(options) if options is not None else [ALLOW_OPTION, REJECT_OPTION],
    }


# -- client capability declaration ------------------------------------------


def test_capabilities_declaration_for_read_grant(tmp_path: Path) -> None:
    bridge, _, _ = _bridge(tmp_path, capabilities=("read",))
    declared = bridge.client_capabilities()
    assert declared == {
        "fs": {"readTextFile": True, "writeTextFile": False},
        "terminal": False,
    }


def test_capabilities_declaration_without_read_grant(tmp_path: Path) -> None:
    bridge, _, _ = _bridge(tmp_path, capabilities=())
    declared = bridge.client_capabilities()
    assert declared["fs"] == {"readTextFile": False, "writeTextFile": False}
    assert declared["terminal"] is False


def test_grant_is_a_snapshot(tmp_path: Path) -> None:
    capabilities = ["read"]
    events: list[MediationEvent] = []
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bridge = PermissionBridge(
        capabilities=capabilities,
        workspace_root=workspace,
        evidence_sink=events.append,
    )
    capabilities.append("write")  # runtime widening must be invisible
    decision = bridge.decide_fs_write(str(workspace / "note.txt"))
    assert decision["decision"] == "deny"


# -- fs mediation ------------------------------------------------------------


def test_fs_read_inside_workspace_allows(tmp_path: Path) -> None:
    bridge, workspace, events = _bridge(tmp_path)
    decision = bridge.decide_fs_read(str(workspace / "doc.md"))
    assert decision["decision"] == "allow"
    assert events[-1].requested_op == "fs_read"
    assert events[-1].decision == "allow"


def test_fs_read_outside_workspace_denies(tmp_path: Path) -> None:
    bridge, _, events = _bridge(tmp_path)
    outside = tmp_path / "elsewhere" / "doc.md"
    decision = bridge.decide_fs_read(str(outside))
    assert decision["decision"] == "deny"
    assert events[-1].decision == "deny"


def test_fs_read_traversal_outside_workspace_denies(tmp_path: Path) -> None:
    bridge, workspace, events = _bridge(tmp_path)
    sneaky = str(workspace / ".." / "elsewhere" / "doc.md")
    assert bridge.decide_fs_read(sneaky)["decision"] == "deny"
    assert events[-1].decision == "deny"


@pytest.mark.parametrize("cwd_name", ["cwd-a", "cwd-b"])
def test_relative_fs_read_resolves_workspace_bound_regardless_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cwd_name: str
) -> None:
    # The decision and the actual read must share one canonical
    # workspace-bound path: a relative request never resolves against the
    # supervisor process cwd, whatever that cwd happens to be.
    bridge, workspace, events = _bridge(tmp_path)
    (workspace / "same.txt").write_text("workspace copy", encoding="utf-8")
    decoy_cwd = tmp_path / cwd_name
    decoy_cwd.mkdir()
    (decoy_cwd / "same.txt").write_text("supervisor cwd copy", encoding="utf-8")
    monkeypatch.chdir(decoy_cwd)

    decision = bridge.decide_fs_read("same.txt")
    assert decision["decision"] == "allow"
    assert decision["resolved_path"] == str((workspace / "same.txt").resolve())
    assert events[-1].decision == "allow"


def test_fs_read_decision_carries_canonical_path_for_absolute_inside(
    tmp_path: Path,
) -> None:
    bridge, workspace, events = _bridge(tmp_path)
    target = workspace / "docs" / "note.md"
    decision = bridge.decide_fs_read(str(target))
    assert decision["decision"] == "allow"
    assert decision["resolved_path"] == str(target.resolve())


def test_purely_relative_traversal_is_denied(tmp_path: Path) -> None:
    bridge, _, events = _bridge(tmp_path)
    decision = bridge.decide_fs_read("../outside.txt")
    assert decision["decision"] == "deny"
    assert "resolved_path" not in decision
    assert events[-1].decision == "deny"


def test_fs_read_without_read_capability_denies(tmp_path: Path) -> None:
    bridge, workspace, events = _bridge(tmp_path, capabilities=())
    assert bridge.decide_fs_read(str(workspace / "doc.md"))["decision"] == "deny"
    assert events[-1].decision == "deny"


def test_fs_write_always_denies_under_first_grant(tmp_path: Path) -> None:
    bridge, workspace, events = _bridge(tmp_path)
    decision = bridge.decide_fs_write(str(workspace / "new.txt"))
    assert decision["decision"] == "deny"
    assert events[-1].requested_op == "fs_write"


# -- permission-request mediation table --------------------------------------


@pytest.mark.parametrize("kind", ["read", "search"])
def test_workspace_scoped_read_like_kinds_allow(tmp_path: Path, kind: str) -> None:
    bridge, _, events = _bridge(tmp_path)
    decision = bridge.decide_permission_request(_request(kind))
    assert decision["decision"] == "allow"
    assert decision["option_id"] == "opt-allow"
    assert events[-1].requested_op == f"permission:{kind}"
    assert events[-1].decision == "allow"
    assert bridge.turn_failed is False


@pytest.mark.parametrize(
    "kind",
    ["edit", "delete", "move", "execute", "fetch", "switch_mode", "other", "think"],
)
def test_mutating_and_other_kinds_deny(tmp_path: Path, kind: str) -> None:
    bridge, _, events = _bridge(tmp_path)
    decision = bridge.decide_permission_request(_request(kind))
    assert decision["decision"] == "deny"
    assert decision["option_id"] == "opt-reject"
    assert events[-1].decision == "deny"


def test_unregistered_kind_denies_by_default(tmp_path: Path) -> None:
    bridge, _, events = _bridge(tmp_path)
    decision = bridge.decide_permission_request(_request("mystery_op"))
    assert decision["decision"] == "deny"
    assert events[-1].decision == "deny"
    assert "unregistered" in events[-1].reason


def test_read_kind_without_read_capability_denies(tmp_path: Path) -> None:
    bridge, _, events = _bridge(tmp_path, capabilities=())
    assert bridge.decide_permission_request(_request("read"))["decision"] == "deny"
    assert events[-1].decision == "deny"


def test_unexpected_unmappable_request_denies_and_fails_turn(tmp_path: Path) -> None:
    bridge, _, events = _bridge(tmp_path)
    decision = bridge.decide_permission_request(_request(None))
    assert decision["decision"] == "deny"
    assert bridge.turn_failed is True
    assert events[-1].decision == "deny"


def test_deny_without_reject_option_falls_back_to_cancel(tmp_path: Path) -> None:
    bridge, _, _ = _bridge(tmp_path)
    decision = bridge.decide_permission_request(
        _request("edit", options=[ALLOW_OPTION])
    )
    assert decision["decision"] == "deny"
    assert decision.get("option_id") is None


def test_every_decision_emits_a_mediation_event(tmp_path: Path) -> None:
    bridge, workspace, events = _bridge(tmp_path)
    bridge.decide_fs_read(str(workspace / "a.md"))
    bridge.decide_fs_write(str(workspace / "b.md"))
    bridge.decide_permission_request(_request("read"))
    bridge.decide_permission_request(_request("execute"))
    bridge.decide_permission_request(_request(None))
    assert len(events) == 5
    assert all(isinstance(event, MediationEvent) for event in events)
    assert all(event.reason for event in events)
    payloads = [event.to_event() for event in events]
    assert all(payload["type"] == "permission_mediation" for payload in payloads)
    assert all(
        set(payload) >= {"type", "requested_op", "decision", "reason"}
        for payload in payloads
    )


# -- write-family completion backstop (A4-S2 grant-violation detection) ------


def _update(update_type: str, **fields):
    payload = {"sessionUpdate": update_type}
    payload.update(fields)
    return payload


def test_write_family_completion_without_grant_flags_violation(
    tmp_path: Path,
) -> None:
    bridge, _, _ = _bridge(tmp_path, capabilities=("read",))
    assert (
        bridge.observe_tool_update(
            _update("tool_call", toolCallId="call-1", kind="edit", status="pending")
        )
        is None
    )
    assert (
        bridge.observe_tool_update(
            _update("tool_call_update", toolCallId="call-1", status="in_progress")
        )
        is None
    )
    violation = bridge.observe_tool_update(
        _update("tool_call_update", toolCallId="call-1", status="completed")
    )
    assert violation is not None
    assert violation["type"] == "permission_violation"
    assert violation["tool_call_id"] == "call-1"
    assert violation["kind"] == "edit"
    assert violation["required_capability"] == "write"
    assert bridge.grant_violation is True


@pytest.mark.parametrize(
    ("kind", "required"),
    [("edit", "write"), ("delete", "delete"), ("move", "move"), ("execute", "execute")],
)
def test_every_write_family_kind_maps_to_its_capability(
    tmp_path: Path, kind: str, required: str
) -> None:
    bridge, _, _ = _bridge(tmp_path, capabilities=("read",))
    violation = bridge.observe_tool_update(
        _update("tool_call", toolCallId="call-9", kind=kind, status="completed")
    )
    assert violation is not None
    assert violation["required_capability"] == required
    assert bridge.grant_violation is True


def test_read_like_completion_never_flags(tmp_path: Path) -> None:
    bridge, _, _ = _bridge(tmp_path, capabilities=("read",))
    bridge.observe_tool_update(
        _update("tool_call", toolCallId="call-2", kind="read", status="pending")
    )
    assert (
        bridge.observe_tool_update(
            _update("tool_call_update", toolCallId="call-2", status="completed")
        )
        is None
    )
    assert bridge.grant_violation is False


def test_failed_write_family_tool_is_the_healthy_deny_shape(tmp_path: Path) -> None:
    # A denied/errored write tool never reached its side effect; only a
    # *completed* write-family tool without the capability is a violation.
    bridge, _, _ = _bridge(tmp_path, capabilities=("read",))
    bridge.observe_tool_update(
        _update("tool_call", toolCallId="call-3", kind="edit", status="pending")
    )
    assert (
        bridge.observe_tool_update(
            _update("tool_call_update", toolCallId="call-3", status="failed")
        )
        is None
    )
    assert bridge.grant_violation is False


def test_granted_capability_suppresses_violation(tmp_path: Path) -> None:
    bridge, _, _ = _bridge(tmp_path, capabilities=("read", "write"))
    assert (
        bridge.observe_tool_update(
            _update("tool_call", toolCallId="call-4", kind="edit", status="completed")
        )
        is None
    )
    assert bridge.grant_violation is False


def test_completion_of_unknown_tool_id_without_kind_does_not_flag(
    tmp_path: Path,
) -> None:
    # Documented residual: a completion whose kind was never observed cannot
    # be proven write-family; the mediated ask/deny launch binding is the
    # prevention layer for those.
    bridge, _, _ = _bridge(tmp_path, capabilities=("read",))
    assert (
        bridge.observe_tool_update(
            _update("tool_call_update", toolCallId="call-never-seen", status="completed")
        )
        is None
    )
    assert bridge.grant_violation is False


# -- B4: option-scope discipline and grant-driven execute mediation ----------

ALLOW_ALWAYS_OPTION = {
    "optionId": "allow_always",
    "name": "Always allow",
    "kind": "allow_always",
}
REJECT_ALWAYS_OPTION = {
    "optionId": "reject_always",
    "name": "Always reject",
    "kind": "reject_always",
}
# The official Claude adapter advertises the always-scoped option FIRST.
CLAUDE_SHAPED_OPTIONS = [
    ALLOW_ALWAYS_OPTION,
    {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
    {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
]


def test_allow_once_is_preferred_over_an_earlier_allow_always(
    tmp_path: Path,
) -> None:
    # Honoring allow_always installs a session-scoped allow rule for the tool:
    # a broad auto-allow that outlives the mediated call.
    bridge, _, events = _bridge(tmp_path)
    decision = bridge.decide_permission_request(
        _request("read", options=CLAUDE_SHAPED_OPTIONS)
    )
    assert decision["decision"] == "allow"
    assert decision["option_id"] == "allow"
    assert events[-1].decision == "allow"


def test_allow_never_selects_an_always_scoped_option(tmp_path: Path) -> None:
    bridge, _, events = _bridge(tmp_path)
    decision = bridge.decide_permission_request(
        _request("read", options=[ALLOW_ALWAYS_OPTION, REJECT_OPTION])
    )
    # No once-scoped allow on offer: fail closed rather than widen the grant.
    assert decision["decision"] == "deny"
    assert decision["option_id"] == "opt-reject"
    assert "once" in events[-1].reason


def test_reject_once_is_preferred_over_an_earlier_reject_always(
    tmp_path: Path,
) -> None:
    bridge, _, _ = _bridge(tmp_path)
    decision = bridge.decide_permission_request(
        _request("edit", options=[REJECT_ALWAYS_OPTION, REJECT_OPTION])
    )
    assert decision["decision"] == "deny"
    assert decision["option_id"] == "opt-reject"


def test_execute_allows_once_only_when_the_grant_carries_execute(
    tmp_path: Path,
) -> None:
    bridge, _, events = _bridge(tmp_path, capabilities=("read", "execute"))
    decision = bridge.decide_permission_request(
        _request("execute", options=CLAUDE_SHAPED_OPTIONS)
    )
    assert decision["decision"] == "allow"
    assert decision["option_id"] == "allow"  # the once-scoped option id
    assert events[-1].requested_op == "permission:execute"
    assert bridge.turn_failed is False
    # The completion backstop agrees: an execute completion under an execute
    # grant is legitimate, so mediation and detection cannot contradict.
    assert (
        bridge.observe_tool_update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tool-1",
                "kind": "execute",
                "status": "completed",
            }
        )
        is None
    )
    assert bridge.grant_violation is False


def test_execute_denies_without_the_execute_grant(tmp_path: Path) -> None:
    bridge, _, events = _bridge(tmp_path, capabilities=("read", "write"))
    decision = bridge.decide_permission_request(
        _request("execute", options=CLAUDE_SHAPED_OPTIONS)
    )
    assert decision["decision"] == "deny"
    assert decision["option_id"] == "reject"
    assert events[-1].decision == "deny"


def test_execute_without_a_once_option_denies_even_when_granted(
    tmp_path: Path,
) -> None:
    bridge, _, _ = _bridge(tmp_path, capabilities=("read", "execute"))
    decision = bridge.decide_permission_request(
        _request("execute", options=[ALLOW_ALWAYS_OPTION, REJECT_OPTION])
    )
    assert decision["decision"] == "deny"


@pytest.mark.parametrize(
    ("kind", "capability"),
    [("edit", "write"), ("delete", "delete"), ("move", "move")],
)
def test_other_write_family_kinds_stay_denied_even_when_granted(
    tmp_path: Path, kind: str, capability: str
) -> None:
    # Scope pin: only `execute` mediation is opened in this slice; the other
    # write-family kinds have no live-canaried mediated-allow path.
    bridge, _, _ = _bridge(tmp_path, capabilities=("read", capability))
    decision = bridge.decide_permission_request(
        _request(kind, options=CLAUDE_SHAPED_OPTIONS)
    )
    assert decision["decision"] == "deny"

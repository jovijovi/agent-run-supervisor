"""C7: default-deny permission bridge over the frozen execution grant."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from agent_run_supervisor.native_acp.permissions import (
    MediationEvent,
    PermissionBridge,
    _canonicalize_past_absence,
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


"""Locations omitted entirely — distinguishable from an empty or malformed
list, because "the adapter declared nothing" and "the adapter declared
something unusable" are different wire facts that must both deny."""
_LOCATIONS_OMITTED = object()


def _request(
    kind: str | None,
    *,
    options=None,
    locations=_LOCATIONS_OMITTED,
    tool_call_id: str | None = "tool-1",
):
    tool_call = {"status": "pending"}
    if tool_call_id is not None:
        # ACP constrains the id to a string, not to a *non-empty* one, so the
        # helper passes whatever string the caller names — ``""`` included.
        tool_call["toolCallId"] = tool_call_id
    if kind is not None:
        tool_call["kind"] = kind
    if locations is not _LOCATIONS_OMITTED:
        tool_call["locations"] = locations
    return {
        "session_id": "external-1",
        "tool_call": tool_call,
        "options": list(options) if options is not None else [ALLOW_OPTION, REJECT_OPTION],
    }


def _read_request(kind: str, workspace: Path, **kwargs):
    """A read-like ask carrying the path evidence an allow now requires."""
    return _request(
        kind,
        locations=[{"path": str((workspace / "doc.md").resolve())}],
        **kwargs,
    )


# -- mediation evidence is legible and correlates with the call it answered --


def test_mediation_evidence_names_the_call_it_refused(tmp_path: Path) -> None:
    """A refusal names what it refused, and correlates by the child's own id.

    The same reason goes back over the wire, so the agent's error and the
    operator's evidence are one text.
    """
    tool_call_id = "call-mediation-3f70"
    events: list[MediationEvent] = []
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    bridge = PermissionBridge(
        capabilities=("read",),
        workspace_root=workspace,
        evidence_sink=events.append,
    )

    decision = bridge.decide_permission_request(
        {
            "session_id": "external-1",
            "tool_call": {
                "toolCallId": tool_call_id,
                "kind": "edit",
                "status": "pending",
            },
            "options": [ALLOW_OPTION, REJECT_OPTION],
        }
    )

    assert decision["decision"] == "deny"
    assert events[0].tool_call_id == tool_call_id
    assert events[0].reason == decision["reason"]
    assert "not permitted" in events[0].reason


def test_an_allow_option_id_travels_back_verbatim(tmp_path: Path) -> None:
    """An option id is an exact child protocol identifier.

    It has to go back over the wire byte-for-byte to select anything. Mediation
    decides on the frozen grant alone, so an id's bytes never make an otherwise
    granted operation fail closed.
    """
    option_id = "option-id-sentinel-2b44"
    events: list[MediationEvent] = []
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    bridge = PermissionBridge(
        capabilities=("read",),
        workspace_root=workspace,
        evidence_sink=events.append,
    )

    decision = bridge.decide_permission_request(
        {
            "session_id": "external-1",
            "tool_call": {
                "toolCallId": "tool-1",
                "kind": "read",
                "status": "pending",
                "locations": [{"path": str((workspace / "doc.md").resolve())}],
            },
            "options": [
                {"optionId": option_id, "name": "Allow", "kind": "allow_once"},
                REJECT_OPTION,
            ],
        }
    )

    assert decision["decision"] == "allow"
    assert decision["option_id"] == option_id


def test_a_deny_still_carries_the_reject_option_id(tmp_path: Path) -> None:
    reject_id = "reject-id-sentinel-9c01"
    events: list[MediationEvent] = []
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    bridge = PermissionBridge(
        capabilities=("read",),
        workspace_root=workspace,
        evidence_sink=events.append,
    )

    decision = bridge.decide_permission_request(
        {
            "session_id": "external-1",
            "tool_call": {"toolCallId": "tool-1", "kind": "edit", "status": "pending"},
            "options": [
                ALLOW_OPTION,
                {"optionId": reject_id, "name": "Reject", "kind": "reject_once"},
            ],
        }
    )

    assert decision["decision"] == "deny"
    assert decision["option_id"] == reject_id


def test_an_ordinary_once_scoped_allow_still_returns_its_option_id(
    tmp_path: Path,
) -> None:
    bridge, workspace, _events = _bridge(tmp_path, capabilities=("read",))

    decision = bridge.decide_permission_request(_read_request("read", workspace))

    assert decision["decision"] == "allow"
    assert decision["option_id"] == ALLOW_OPTION["optionId"]


def test_grant_violation_evidence_names_the_kind_and_capability(
    tmp_path: Path,
) -> None:
    tool_call_id = "call-violation-91ab"
    bridge, _workspace, _events = _bridge(tmp_path, capabilities=("read",))

    bridge.observe_tool_update(
        {
            "sessionUpdate": "tool_call",
            "toolCallId": tool_call_id,
            "kind": "edit",
            "status": "pending",
        }
    )
    violation = bridge.observe_tool_update(
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": tool_call_id,
            "status": "completed",
        }
    )

    assert violation is not None
    assert bridge.grant_violation is True
    assert violation["tool_call_id"] == tool_call_id
    assert violation["required_capability"] == "write"
    assert violation["violation_class"] == "missing_grant_capability"
    assert "edit" in (bridge.grant_violation_reason or "")


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
    bridge, workspace, events = _bridge(tmp_path)
    decision = bridge.decide_permission_request(_read_request(kind, workspace))
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


# -- read-like path evidence: a read grant says whether, never what ----------


@pytest.mark.parametrize("kind", ["read", "search"])
def test_read_like_permission_without_locations_denies(
    tmp_path: Path, kind: str
) -> None:
    # A `read` grant answers *whether* the agent may read, never *what*. With
    # no declared location there is no evidence the target is inside the bound
    # workspace, so the only honest answer is deny.
    bridge, _workspace, events = _bridge(tmp_path)

    decision = bridge.decide_permission_request(_request(kind))

    assert decision["decision"] == "deny"
    assert decision["option_id"] == REJECT_OPTION["optionId"]
    assert events[-1].requested_op == f"permission:{kind}"
    assert events[-1].reason == "read-like permission request has no usable locations"


@pytest.mark.parametrize(
    "locations",
    [
        [],
        [None],
        [{}],
        [{"path": ""}],
        [{"path": 7}],
        [{"path": "relative/doc.md"}],
        [{"path": "doc.md"}],
        "not-a-list",
        {"path": "/workspace/doc.md"},
    ],
    ids=[
        "empty-list",
        "null-entry",
        "no-path-key",
        "empty-path",
        "non-string-path",
        "relative-path",
        "bare-name",
        "not-a-list",
        "single-mapping",
    ],
)
def test_read_like_permission_with_unusable_location_denies(
    tmp_path: Path, locations
) -> None:
    # A workspace-relative path is exactly what fs/read_text_file accepts, and
    # exactly what a permission prompt may not: the ACP contract declares
    # ToolCallLocation.path absolute, so a relative one is unproven, not lenient.
    bridge, _workspace, events = _bridge(tmp_path)

    decision = bridge.decide_permission_request(
        _request("read", locations=locations)
    )

    assert decision["decision"] == "deny"
    assert events[-1].decision == "deny"
    assert "location" in events[-1].reason


def test_read_like_permission_with_an_unparseable_path_denies(
    tmp_path: Path,
) -> None:
    # A path the platform cannot even parse is malformed, not merely
    # uncontained: mediation still owes a recorded deny decision, never an
    # exception escaping into the SDK's permission callback.
    bridge, _workspace, events = _bridge(tmp_path)

    decision = bridge.decide_permission_request(
        _request("read", locations=[{"path": "/embedded\x00null"}])
    )

    assert decision["decision"] == "deny"
    assert events[-1].reason == "read-like permission request has an invalid location"


def test_read_like_permission_with_a_symlink_loop_denies(tmp_path: Path) -> None:
    # A two-node symlink loop is unresolvable path evidence: containment can
    # never be proven for it. Resolution answers differently per Python — an
    # escaping RuntimeError on 3.11/3.12, a partially resolved path on 3.13+ —
    # so mediation may rely on neither: it owes the same recorded deny on every
    # supported version, never an exception in the SDK's permission callback
    # and never an allow off a half-resolved path.
    bridge, workspace, events = _bridge(tmp_path)
    first = workspace / "loop-a"
    second = workspace / "loop-b"
    first.symlink_to(second)
    second.symlink_to(first)

    decision = bridge.decide_permission_request(
        _request("read", locations=[{"path": str(first)}])
    )

    assert decision["decision"] == "deny"
    assert events[-1].decision == "deny"
    assert events[-1].reason == "read-like permission request has an invalid location"
    assert decision["reason"] == events[-1].reason
    # Categorical and path-free, like every other location refusal.
    assert str(first) not in events[-1].reason
    assert str(first) not in decision["reason"]


def test_read_like_permission_denies_a_loop_that_resolves_partially(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The half of the version matrix this interpreter cannot execute: on
    # 3.13/3.14 lenient resolution can stop at the loop and return a partially
    # resolved path that still looks workspace-internal. Pinned here because
    # catching the 3.11/3.12 RuntimeError alone would leave that path allowing.
    # A strict failure that is not "the target is simply absent" must deny, not
    # fall back to the lenient answer.
    bridge, workspace, events = _bridge(tmp_path)
    looks_internal = (workspace / "doc.md").resolve()

    def fake_resolve(self: Path, strict: bool = False) -> Path:
        if strict:
            raise OSError(errno.ELOOP, os.strerror(errno.ELOOP))
        return looks_internal

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    decision = bridge.decide_permission_request(
        _request("read", locations=[{"path": str(workspace / "loop-a")}])
    )

    assert decision["decision"] == "deny"
    assert events[-1].decision == "deny"
    assert events[-1].reason == "read-like permission request has an invalid location"


@pytest.mark.parametrize("kind", ["read", "search"])
@pytest.mark.parametrize(
    "tail",
    [("loop-a",), ("loop-a", "child.txt"), ("loop-a", "..", "doc.md")],
    ids=["loop-is-the-target", "loop-is-intermediate", "loop-popped-back-off"],
)
def test_read_like_permission_denies_a_loop_reached_past_a_missing_component(
    tmp_path: Path, kind: str, tail: tuple[str, ...]
) -> None:
    # Real symlinks, no monkeypatch: the strict probe stops at the *first*
    # absent component and reports plain absence, so a loop sitting in the
    # lexical suffix behind it is never probed at all. "Strict said
    # FileNotFoundError" therefore does not mean "an ordinary missing target" —
    # only that nothing before the absent component failed. Resolving the rest
    # leniently hands back a path that reads as fully resolved while still
    # holding the unresolved loop (3.13/3.14), and containment checked against
    # an unresolved symlink is not containment.
    #
    # The class, not one spelling: the loop is unresolvable evidence wherever
    # it sits behind the absent component — as the target, in the middle, or
    # even lexically popped back off again, which is the shape lenient
    # resolution erases entirely on *every* version. All of them deny, exactly
    # like the same loop declared directly.
    bridge, workspace, events = _bridge(tmp_path)
    first = workspace / "loop-a"
    second = workspace / "loop-b"
    first.symlink_to(second)
    second.symlink_to(first)
    location = workspace.joinpath("missing", "..", *tail)

    decision = bridge.decide_permission_request(
        _request(kind, locations=[{"path": str(location)}])
    )

    assert decision["decision"] == "deny"
    assert events[-1].decision == "deny"
    assert events[-1].reason == "read-like permission request has an invalid location"
    assert decision["reason"] == events[-1].reason
    # Categorical and path-free, like every other location refusal.
    assert str(location) not in events[-1].reason
    assert str(location) not in decision["reason"]


def test_canonicalizing_past_absence_refuses_a_relative_path() -> None:
    # The walk starts at the path's anchor, so a relative path has none: it
    # would resolve against the supervisor's cwd, a root no request named. That
    # precondition is enforced rather than commented, and the ValueError it
    # raises is already a recorded deny at the only call site.
    with pytest.raises(ValueError):
        _canonicalize_past_absence(Path("relative/doc.md"))


@pytest.mark.parametrize("created", [False, True])
def test_read_like_permission_allows_an_ordinary_target_past_a_missing_component(
    tmp_path: Path, created: bool
) -> None:
    # The other side of that boundary, pinned so the loop repair cannot become
    # "an absent component anywhere means deny". The same missing-prefix
    # spelling with no symlink evidence in the suffix is still an ordinary
    # containment question — `..` keeps its normal meaning and the answer is
    # inside — whether or not the target behind it exists yet.
    bridge, workspace, events = _bridge(tmp_path)
    target = workspace / "doc.md"
    if created:
        target.write_text("doc", encoding="utf-8")
    location = workspace / "missing" / ".." / "doc.md"

    decision = bridge.decide_permission_request(
        _request("read", locations=[{"path": str(location)}])
    )

    assert decision["decision"] == "allow"
    assert decision["option_id"] == ALLOW_OPTION["optionId"]
    assert events[-1].decision == "allow"


@pytest.mark.parametrize("kind", ["read", "search"])
def test_read_like_permission_for_a_missing_workspace_target_allows(
    tmp_path: Path, kind: str
) -> None:
    # Positive control for the strict probe, pinned so the loop repair cannot
    # widen into a new refusal: strict resolution also fails for an ordinary
    # not-yet-created file, and that one case — and only that one — falls back
    # to the lenient canonicalization the shared fs/read_text_file path uses.
    # Asking about a workspace-internal path before it exists is legitimate
    # (searching for it is how an agent learns it does not), so the containment
    # question still has an answer and that answer is inside.
    bridge, workspace, events = _bridge(tmp_path)
    target = workspace / "not-created-yet" / "doc.md"
    assert not target.exists()
    assert not target.parent.exists()

    decision = bridge.decide_permission_request(
        _request(kind, locations=[{"path": str(target)}])
    )

    assert decision["decision"] == "allow"
    assert decision["option_id"] == ALLOW_OPTION["optionId"]
    assert events[-1].requested_op == f"permission:{kind}"
    assert events[-1].decision == "allow"


def _double_slash_names_the_root() -> bool:
    """Whether this host gives ``//`` the POSIX/Linux answer.

    POSIX leaves a pathname beginning with exactly two slashes
    implementation-defined; Linux gives it no separate meaning, so ``//x`` and
    ``/x`` are one directory. The tests below assert that equivalence, so they
    ask the host whether it holds rather than assuming it.
    """
    if os.name != "posix":
        return False
    try:
        return os.path.samestat(os.stat("//"), os.stat(os.sep))
    except OSError:
        return False


_LINUX_ROOT_SPELLINGS = pytest.mark.skipif(
    not _double_slash_names_the_root(),
    reason="host does not give '//' the POSIX/Linux root-equivalent meaning",
)


@_LINUX_ROOT_SPELLINGS
@pytest.mark.parametrize("kind", ["read", "search"])
@pytest.mark.parametrize(
    "leading_slashes", [1, 2], ids=["single-slash", "double-slash"]
)
def test_equivalent_root_spellings_decide_one_missing_target_alike(
    tmp_path: Path, kind: str, leading_slashes: int
) -> None:
    # Paired positive control. On this host ``//x`` and ``/x`` are the same
    # directory, so the two spellings are one containment question and must get
    # one answer: an ordinary not-yet-created workspace-internal target allows.
    #
    # The canonicalization walk judges every component against real filesystem
    # evidence except the anchor, which it would otherwise carry through as the
    # caller's literal spelling — and a ``//``-anchored result compares unequal
    # to the ``/``-anchored workspace root, so an ordinary missing target reads
    # as outside the workspace purely because of how the agent spelled the root.
    bridge, workspace, events = _bridge(tmp_path)
    target = workspace / "not-created-yet" / "doc.md"
    assert not target.exists()
    declared = "/" * leading_slashes + str(target).lstrip("/")
    assert Path(declared).is_absolute()

    decision = bridge.decide_permission_request(
        _request(kind, locations=[{"path": declared}])
    )

    assert decision["decision"] == "allow"
    assert decision["option_id"] == ALLOW_OPTION["optionId"]
    assert events[-1].requested_op == f"permission:{kind}"
    assert events[-1].decision == "allow"


@_LINUX_ROOT_SPELLINGS
def test_a_root_spelled_symlink_target_decides_like_the_directory_it_names(
    tmp_path: Path,
) -> None:
    # The same anchor, reached from the other source that supplies one: a
    # symlink's stored target text. ``os.readlink`` hands back exactly the bytes
    # the link holds, so a link written with the ``//`` spelling restarts the
    # walk at that anchor even though the declared location is ordinary. The
    # link names a workspace-internal directory, so the missing file under it is
    # the same ordinary containment question and allows.
    bridge, workspace, events = _bridge(tmp_path)
    nested = workspace / "nested"
    assert not nested.exists()
    link = workspace / "link"
    link.symlink_to("//" + str(nested).lstrip("/"))

    decision = bridge.decide_permission_request(
        _request("read", locations=[{"path": str(link / "doc.md")}])
    )

    assert decision["decision"] == "allow"
    assert decision["option_id"] == ALLOW_OPTION["optionId"]
    assert events[-1].decision == "allow"


def test_read_permission_outside_workspace_denies(tmp_path: Path) -> None:
    bridge, _workspace, events = _bridge(tmp_path)
    outside = (tmp_path / "outside.txt").resolve()

    decision = bridge.decide_permission_request(
        _request("read", locations=[{"path": str(outside)}])
    )

    assert decision["decision"] == "deny"
    assert (
        events[-1].reason
        == "read-like permission location is outside the bound workspace"
    )
    # Categorical, ARS-authored, and path-free: the refusal names the class of
    # failure, never the child-chosen path it refused.
    assert str(outside) not in events[-1].reason
    assert str(outside) not in decision["reason"]


def test_read_permission_traversal_outside_workspace_denies(tmp_path: Path) -> None:
    # Lexically absolute, but `..` walks it out of the workspace.
    bridge, workspace, events = _bridge(tmp_path)
    sneaky = str(workspace / ".." / "elsewhere" / "doc.md")
    assert Path(sneaky).is_absolute()

    decision = bridge.decide_permission_request(
        _request("search", locations=[{"path": sneaky}])
    )

    assert decision["decision"] == "deny"
    assert (
        events[-1].reason
        == "read-like permission location is outside the bound workspace"
    )


def test_read_permission_symlink_escape_denies(tmp_path: Path) -> None:
    # The declared path is inside the workspace; its canonical target is not.
    # Containment is decided after symlink resolution, like fs/read_text_file.
    bridge, workspace, events = _bridge(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = workspace / "outside-link"
    link.symlink_to(outside)

    decision = bridge.decide_permission_request(
        _request("read", locations=[{"path": str(link.absolute())}])
    )

    assert decision["decision"] == "deny"
    assert (
        events[-1].reason
        == "read-like permission location is outside the bound workspace"
    )


def test_read_permission_denies_when_any_location_is_outside(tmp_path: Path) -> None:
    # Every declared location has to be inside: one outside entry among inside
    # ones is still an outside read.
    bridge, workspace, events = _bridge(tmp_path)
    locations = [
        {"path": str((workspace / "inside.txt").resolve())},
        {"path": str((tmp_path / "outside.txt").resolve())},
    ]

    decision = bridge.decide_permission_request(
        _request("search", locations=locations)
    )

    assert decision["decision"] == "deny"
    assert events[-1].decision == "deny"


def test_read_like_permission_never_takes_a_path_from_untrusted_fields(
    tmp_path: Path,
) -> None:
    # rawInput/_meta/title/content are adapter-private and arbitrary. Only the
    # protocol-declared locations list is path authority, so a prompt that
    # carries an inside path *anywhere else* still denies.
    bridge, workspace, events = _bridge(tmp_path)
    inside = str((workspace / "doc.md").resolve())
    request = _request("read")
    request["tool_call"].update(
        {
            "title": f"Read {inside}",
            "rawInput": {"path": inside, "file_path": inside},
            "_meta": {"path": inside},
            "content": [{"type": "content", "path": inside}],
        }
    )

    decision = bridge.decide_permission_request(request)

    assert decision["decision"] == "deny"
    assert events[-1].reason == "read-like permission request has no usable locations"


def test_search_permission_allows_when_every_location_is_inside(
    tmp_path: Path,
) -> None:
    bridge, workspace, events = _bridge(tmp_path, capabilities=("read", "search"))
    locations = [
        {"path": str(workspace.resolve())},
        {"path": str((workspace / "a.txt").resolve())},
        {"path": str((workspace / "nested" / "b.txt").resolve())},
    ]

    decision = bridge.decide_permission_request(
        _request("search", locations=locations)
    )

    assert decision["decision"] == "allow"
    assert decision["option_id"] == ALLOW_OPTION["optionId"]
    assert events[-1].decision == "allow"
    assert events[-1].reason == "workspace-scoped read-like operation under read grant"


def test_read_like_path_evidence_never_widens_a_grant_without_read(
    tmp_path: Path,
) -> None:
    # Path proof is an additional condition, never a substitute for the grant.
    bridge, workspace, events = _bridge(tmp_path, capabilities=())

    decision = bridge.decide_permission_request(_read_request("read", workspace))

    assert decision["decision"] == "deny"
    assert "not permitted by the frozen grant" in events[-1].reason


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
    bridge.decide_permission_request(_read_request("read", workspace))
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
    assert violation["violation_class"] == "missing_grant_capability"
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


# -- a completion contradicting ARS's own denial (cooperative-protocol break) -


def test_completed_tool_call_after_permission_deny_flags_violation(
    tmp_path: Path,
) -> None:
    # Mediation is cooperative: ARS answered "no" and the agent reported the
    # very same call completed. ARS cannot undo the side effect, but a Run that
    # persists as completed would read as if the denial held.
    bridge, _workspace, _events = _bridge(tmp_path, capabilities=("read",))
    assert bridge.decide_permission_request(_request("edit"))["decision"] == "deny"

    violation = bridge.observe_tool_update(
        _update("tool_call_update", toolCallId="tool-1", status="completed")
    )

    assert violation is not None
    assert violation["type"] == "permission_violation"
    assert violation["violation_class"] == "completed_after_deny"
    assert violation["tool_call_id"] == "tool-1"
    assert violation["kind"] == "edit"
    assert bridge.grant_violation is True


def test_failed_tool_call_after_permission_deny_is_healthy(tmp_path: Path) -> None:
    # The honored-denial shape. An agent that refuses the operation it was
    # denied is behaving correctly and must not fail the Run.
    bridge, _workspace, _events = _bridge(tmp_path, capabilities=("read",))
    assert bridge.decide_permission_request(_request("edit"))["decision"] == "deny"

    violation = bridge.observe_tool_update(
        _update("tool_call_update", toolCallId="tool-1", status="failed")
    )

    assert violation is None
    assert bridge.grant_violation is False


def test_completed_tool_after_no_once_option_deny_flags_violation(
    tmp_path: Path,
) -> None:
    # The grant carries write, so the write-family backstop would never flag
    # this: what was contradicted is the *decision* ARS actually made, which
    # denied because no once-scoped option was on offer.
    bridge, _workspace, _events = _bridge(tmp_path, capabilities=("read", "write"))
    request = _request("edit", options=[ALLOW_ALWAYS_OPTION, REJECT_OPTION])
    assert bridge.decide_permission_request(request)["decision"] == "deny"

    violation = bridge.observe_tool_update(
        _update("tool_call_update", toolCallId="tool-1", kind="edit", status="completed")
    )

    assert violation is not None
    assert violation["violation_class"] == "completed_after_deny"
    assert violation["kind"] == "edit"
    assert bridge.grant_violation is True


def test_completed_read_after_no_once_option_deny_flags_violation(
    tmp_path: Path,
) -> None:
    # Read-like kinds are outside the write-family backstop entirely, so only
    # denial correlation can catch a read that completed after its refusal.
    bridge, workspace, _events = _bridge(tmp_path, capabilities=("read",))
    request = _read_request(
        "read", workspace, options=[ALLOW_ALWAYS_OPTION, REJECT_OPTION]
    )
    assert bridge.decide_permission_request(request)["decision"] == "deny"

    violation = bridge.observe_tool_update(
        _update("tool_call_update", toolCallId="tool-1", status="completed")
    )

    assert violation is not None
    assert violation["violation_class"] == "completed_after_deny"
    assert violation["kind"] == "read"
    assert bridge.grant_violation is True


def test_completed_read_after_a_location_deny_flags_violation(tmp_path: Path) -> None:
    # The two fixes compose: an outside-location read is denied, and completing
    # it anyway is the contradiction.
    bridge, _workspace, _events = _bridge(tmp_path, capabilities=("read",))
    outside = str((tmp_path / "outside.txt").resolve())
    assert (
        bridge.decide_permission_request(
            _request("read", locations=[{"path": outside}])
        )["decision"]
        == "deny"
    )

    violation = bridge.observe_tool_update(
        _update("tool_call_update", toolCallId="tool-1", status="completed")
    )

    assert violation is not None
    assert violation["violation_class"] == "completed_after_deny"
    assert violation["kind"] == "read"
    # ARS-authored and categorical: no path, option id, or child payload.
    assert outside not in violation["reason"]
    assert set(violation) == {
        "type",
        "violation_class",
        "tool_call_id",
        "kind",
        "reason",
    }


def test_the_first_denial_for_a_tool_call_is_the_one_remembered(
    tmp_path: Path,
) -> None:
    # Once ARS denied a call, a later duplicate prompt for the same id cannot
    # erase or rewrite that fact.
    bridge, _workspace, _events = _bridge(tmp_path, capabilities=("read",))
    bridge.decide_permission_request(_request("edit"))
    bridge.decide_permission_request(_request("delete"))

    violation = bridge.observe_tool_update(
        _update("tool_call_update", toolCallId="tool-1", status="completed")
    )

    assert violation is not None
    assert violation["kind"] == "edit"


def test_an_allowed_read_that_completes_is_not_a_violation(tmp_path: Path) -> None:
    bridge, workspace, _events = _bridge(tmp_path, capabilities=("read",))
    assert (
        bridge.decide_permission_request(_read_request("read", workspace))["decision"]
        == "allow"
    )

    assert (
        bridge.observe_tool_update(
            _update("tool_call_update", toolCallId="tool-1", status="completed")
        )
        is None
    )
    assert bridge.grant_violation is False


def test_completed_tool_call_after_an_empty_string_id_deny_flags_violation(
    tmp_path: Path,
) -> None:
    # ACP constrains ``toolCallId`` to a string, not to a non-empty one, so
    # ``""`` is a *present* id that correlates exactly like any other: the agent
    # that reported this call completed contradicted the refusal ARS issued for
    # it. Read-like kinds sit outside the write-family backstop entirely, so
    # denial correlation is the only thing that can catch this at all — an id
    # judged for truthiness instead of type drops the whole contradiction.
    bridge, _workspace, _events = _bridge(tmp_path, capabilities=("read",))
    assert (
        bridge.decide_permission_request(_request("read", tool_call_id=""))["decision"]
        == "deny"
    )

    violation = bridge.observe_tool_update(
        _update("tool_call_update", toolCallId="", status="completed")
    )

    assert violation is not None
    assert violation["type"] == "permission_violation"
    assert violation["violation_class"] == "completed_after_deny"
    assert violation["tool_call_id"] == ""
    assert violation["kind"] == "read"
    assert bridge.grant_violation is True


def test_an_empty_string_id_denial_correlates_only_that_call(tmp_path: Path) -> None:
    # The other half of treating ``""`` as a protocol string: it is one specific
    # id, never a stand-in for "some id" or for an absent one. A completion
    # naming a different call, or naming none, stays uncorrelated.
    bridge, _workspace, _events = _bridge(tmp_path, capabilities=("read",))
    assert (
        bridge.decide_permission_request(_request("read", tool_call_id=""))["decision"]
        == "deny"
    )

    assert (
        bridge.observe_tool_update(
            _update("tool_call_update", toolCallId="other-call", status="completed")
        )
        is None
    )
    assert bridge.observe_tool_update(_update("tool_call_update", status="completed")) is None
    assert bridge.grant_violation is False


def test_a_denial_without_a_tool_call_id_correlates_nothing(tmp_path: Path) -> None:
    # A prompt that names no call cannot be correlated with any completion —
    # and must not turn an unrelated completed call into a violation.
    bridge, _workspace, _events = _bridge(tmp_path, capabilities=("read",))
    decision = bridge.decide_permission_request(
        {
            "session_id": "external-1",
            "tool_call": {"kind": "edit", "status": "pending"},
            "options": [ALLOW_OPTION, REJECT_OPTION],
        }
    )
    assert decision["decision"] == "deny"

    assert (
        bridge.observe_tool_update(
            _update("tool_call_update", toolCallId="some-other-call", status="completed")
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
OMP_SHAPED_OPTIONS = [
    {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
    {"optionId": "allow_always", "name": "Always allow", "kind": "allow_always"},
    {"optionId": "reject_once", "name": "Reject once", "kind": "reject_once"},
    {"optionId": "reject_always", "name": "Always reject", "kind": "reject_always"},
]


def test_allow_once_is_preferred_over_an_earlier_allow_always(
    tmp_path: Path,
) -> None:
    # Honoring allow_always installs a session-scoped allow rule for the tool:
    # a broad auto-allow that outlives the mediated call.
    bridge, workspace, events = _bridge(tmp_path)
    decision = bridge.decide_permission_request(
        _read_request("read", workspace, options=CLAUDE_SHAPED_OPTIONS)
    )
    assert decision["decision"] == "allow"
    assert decision["option_id"] == "allow"
    assert events[-1].decision == "allow"


def test_allow_never_selects_an_always_scoped_option(tmp_path: Path) -> None:
    # Path evidence is present and inside, so the *only* thing this refusal can
    # be about is option scope.
    bridge, workspace, events = _bridge(tmp_path)
    decision = bridge.decide_permission_request(
        _read_request(
            "read", workspace, options=[ALLOW_ALWAYS_OPTION, REJECT_OPTION]
        )
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


def test_omp_shaped_execute_selects_allow_once_and_never_allow_always(
    tmp_path: Path,
) -> None:
    bridge, _, _ = _bridge(tmp_path, capabilities=("read", "execute"))
    decision = bridge.decide_permission_request(
        _request("execute", options=OMP_SHAPED_OPTIONS)
    )

    assert decision == {
        "decision": "allow",
        "reason": "execute permitted once by the frozen grant",
        "option_id": "allow_once",
    }


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
def test_write_family_kinds_allow_once_with_the_matching_grant(
    tmp_path: Path, kind: str, capability: str
) -> None:
    bridge, _, events = _bridge(tmp_path, capabilities=("read", capability))
    decision = bridge.decide_permission_request(
        _request(kind, options=CLAUDE_SHAPED_OPTIONS)
    )

    assert decision["decision"] == "allow"
    assert decision["option_id"] == "allow"
    assert events[-1].requested_op == f"permission:{kind}"
    assert events[-1].decision == "allow"
    assert events[-1].reason == decision["reason"]
    assert events[-1].tool_call_id == "tool-1"


@pytest.mark.parametrize(
    ("kind", "mismatched_capability"),
    [("edit", "delete"), ("delete", "move"), ("move", "write")],
)
def test_write_family_kinds_deny_without_the_matching_grant(
    tmp_path: Path, kind: str, mismatched_capability: str
) -> None:
    bridge, _, events = _bridge(
        tmp_path, capabilities=("read", mismatched_capability)
    )
    decision = bridge.decide_permission_request(
        _request(kind, options=CLAUDE_SHAPED_OPTIONS)
    )

    assert decision["decision"] == "deny"
    assert decision["option_id"] == "reject"
    assert events[-1].requested_op == f"permission:{kind}"
    assert events[-1].decision == "deny"
    assert events[-1].reason == decision["reason"]
    assert events[-1].tool_call_id == "tool-1"


@pytest.mark.parametrize(
    ("kind", "capability"),
    [("edit", "write"), ("delete", "delete"), ("move", "move")],
)
def test_write_family_kinds_deny_without_a_once_option_even_when_granted(
    tmp_path: Path, kind: str, capability: str
) -> None:
    bridge, _, events = _bridge(tmp_path, capabilities=("read", capability))
    decision = bridge.decide_permission_request(
        _request(kind, options=[ALLOW_ALWAYS_OPTION, REJECT_OPTION])
    )

    assert decision["decision"] == "deny"
    assert decision["option_id"] == "opt-reject"
    assert events[-1].requested_op == f"permission:{kind}"
    assert events[-1].decision == "deny"
    assert events[-1].reason == decision["reason"]
    assert events[-1].reason == "no once-scoped allow option offered; denying"
    assert events[-1].tool_call_id == "tool-1"

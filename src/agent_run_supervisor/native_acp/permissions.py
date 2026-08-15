"""Default-deny permission mediation over the frozen execution grant (R7).

This is cooperative-agent policy enforcement, not an OS sandbox: the bridge
maps registered ACP permission/filesystem request classes to deterministic
allow/deny decisions, records every decision as redacted mediation evidence,
and never widens or re-reads the grant at runtime (snapshot only). Unknown
operation classes deny by default; an unmappable/unexpected permission
prompt additionally flags the turn as failed, because a prompt nobody can answer
is not a decision and must not read as one.

Every allow is *once-scoped by construction*: the bridge selects an
``allow_once`` option or denies, so no decision can install an agent-side rule
that outlives the mediated call. A read-like allow additionally needs *path*
evidence: the grant says whether the agent may read, and only the request's
protocol-declared locations say what, so a read/search prompt allows only when
every declared location is provably inside the bound workspace.

Because mediation is cooperative, the bridge also remembers which calls it
denied: a tool call that reports ``completed`` after ARS refused it broke the
protocol, and the Run must not persist as if the refusal held.
"""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

# Registered read-like ACP tool kinds that the first-E2E read-only grant may
# allow inside the bound workspace — and only with declared locations proving
# the target is inside it. Every other registered kind — and any unregistered
# kind — denies.
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

# Option-scope discipline. Mediation may only ever return a *once*-scoped
# option: an always-scoped allow makes the agent install a session-scoped rule
# for that tool, which outlives the mediated call and auto-allows every later
# one — a broad auto-allow the frozen grant never approved. The official Claude
# adapter advertises ``allow_always`` first, so first-match scanning would pick
# it; the preference order below is therefore explicit, and no always-scoped
# allow is ever selected (a prompt offering only that form denies fail-closed).
_ALLOW_OPTION_PREFERENCE = ("allow_once",)
_REJECT_OPTION_PREFERENCE = ("reject_once", "reject_always")

# Write-family ACP tool kinds and the grant capability each one requires for
# both permission mediation and the completion backstop. A write-family tool
# call that reaches ``completed`` without that capability in the frozen grant
# is a grant violation: the agent performed a side effect the grant never
# allowed and no mediation could have legitimately approved. This is the
# honest fail-closed backstop behind the client-mediated ask/deny launch
# binding — detection, never prevention (the tool already ran).
_WRITE_FAMILY_REQUIRED_CAPABILITY: dict[str, str] = {
    "edit": "write",
    "delete": "delete",
    "move": "move",
    "execute": "execute",
}


def _eloop_as_os_error(resolve: Callable[[], Path]) -> Path:
    """Run one resolution under a single exception vocabulary.

    CPython 3.11/3.12 translate the platform's ``ELOOP`` into ``RuntimeError``,
    a class no filesystem caller expects and none of ARS's fail-closed handlers
    catch, so it escapes as an unhandled error instead of a decision. Undo that
    translation: the underlying fact is an ``OSError``, and the caller already
    owes one recorded decision for the whole ``OSError`` family.
    """
    try:
        return resolve()
    except RuntimeError as exc:
        raise OSError(errno.ELOOP, os.strerror(errno.ELOOP)) from exc


# How many symlink hops one declared location may spend before the walk below
# calls the chain unresolvable. Matches the kernel's own MAXSYMLINKS budget:
# past it, "this path has a meaning" is no longer a claim anyone can check.
_SYMLINK_HOP_LIMIT = 40


def _canonical_anchor(anchor: str) -> Path:
    """The root an absolute path actually starts from, judged by the host.

    The walk below canonicalizes every component against real filesystem
    evidence except one: the anchor, which it would otherwise carry through as
    the literal text of whoever wrote it — the declared location, or a symlink's
    stored target. POSIX leaves a pathname beginning with *exactly* two slashes
    implementation-defined, and ``pathlib`` faithfully preserves that spelling,
    so on a host that gives ``//`` no separate meaning the walk still ends at a
    ``//``-anchored path that compares unequal to the ``/``-anchored workspace
    root. One directory, two spellings, two different containment answers — for
    an ordinary target that was never outside anything.

    Filesystem identity is the evidence that settles it: same device and same
    inode is the same directory, so the walk starts from the single canonical
    root. A host that really does give ``//`` a root of its own keeps the anchor
    that was declared, an anchor carrying more than separators (a drive, a UNC
    share) names its own root and is left alone, and an anchor the host cannot
    stat at all leaves as ``OSError`` — which the caller already owes one
    recorded decision for.
    """
    declared = Path(anchor)
    root = Path(os.sep)
    if declared == root or anchor.strip(os.sep):
        return declared
    if os.path.samestat(os.stat(anchor), os.stat(os.sep)):
        return root
    return declared


def _canonicalize_past_absence(candidate: Path) -> Path:
    """Canonicalize an absolute path one component at a time, or raise.

    Strict resolution stops at the *first* component it cannot resolve, so a
    ``FileNotFoundError`` says only "this component is absent" — it says
    nothing about the lexical components behind it, which a ``..`` can walk
    straight back into existing filesystem. Lenient resolution answers that
    remainder by swallowing every failure alike: an absent component and a
    symlink loop both come back as literal text inside a path that reads as
    fully resolved, and containment checked against an unresolved symlink is
    not containment.

    So the remainder is walked here instead, where each component is judged on
    its own evidence: an absent one canonicalizes to itself (a not-yet-created
    target is a legitimate containment question), an existing one is traversed
    only if it can be, and a symlink is followed to what it actually names.
    Anything that cannot be resolved — a loop, a non-directory in the middle,
    an unreadable ancestor — leaves as ``OSError`` for the caller to record as
    a decision. The walk never consults ``Path.resolve``, so it answers the
    same way on every supported Python.
    """
    if not candidate.is_absolute():
        # The walk starts at the anchor, so a relative path has no honest
        # starting point here: resolving it would silently mean "relative to
        # the supervisor's cwd", a root the request never named.
        raise ValueError("declared location must be absolute to canonicalize")
    resolved = _canonical_anchor(candidate.anchor)
    pending = list(candidate.parts[1:])
    hops = 0
    while pending:
        part = pending.pop(0)
        if part == "..":
            # Normal parent semantics, on a prefix this walk keeps canonical:
            # with symlinks already followed, the parent is the lexical one.
            resolved = resolved.parent
            continue
        step = resolved / part
        try:
            entry = os.lstat(step)
        except FileNotFoundError:
            # Nothing is there to resolve, so the component canonicalizes to
            # itself. This — and only this — is the ordinary missing target.
            resolved = step
            continue
        if not stat.S_ISLNK(entry.st_mode):
            if pending and not stat.S_ISDIR(entry.st_mode):
                # Nothing can be reached *through* a non-directory; the kernel
                # would refuse the traversal, and so does the walk.
                raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(step))
            resolved = step
            continue
        hops += 1
        if hops > _SYMLINK_HOP_LIMIT:
            raise OSError(errno.ELOOP, os.strerror(errno.ELOOP), str(step))
        target = Path(os.readlink(step))
        if target.is_absolute():
            resolved = _canonical_anchor(target.anchor)
            pending = list(target.parts[1:]) + pending
        else:
            # A relative link is relative to the directory holding it, which is
            # the canonical prefix the walk is standing on.
            pending = list(target.parts) + pending
    return resolved


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
        # Every denial this bridge issued, by the call it refused → the
        # ARS-recorded operation it was asked for. A completion for one of
        # these contradicts a decision ARS actually made.
        self._denied_tool_calls: dict[str, str] = {}

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
        # The same reason goes back over the wire, so an operator reading the
        # evidence and the agent reading the refusal see one text.
        return {"decision": decision, "reason": reason}

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

    def _resolve_read_like_location(self, path: str) -> Path:
        """Canonicalize one *declared* read-like location, or raise.

        Scoped to permission prompts, which is why it is not the shared
        ``_resolve_workspace_path``: ``fs/read_text_file`` mediates a path the
        client is about to open itself and its behaviour is unchanged, while a
        prompt carries a path nobody has opened and ARS must still answer.

        Here the probe is strict, because strict is the only mode that answers
        the same way on every supported Python: lenient resolution raises
        ``RuntimeError`` on 3.11/3.12 when a symlink loop is reached, and on
        3.13/3.14 may instead hand back a partially resolved path a containment
        check would wrongly accept. Every resolution failure that is not plain
        absence (a loop, an untraversable ancestor, a non-directory component)
        reaches the caller as ``OSError`` and becomes a recorded decision.

        ``FileNotFoundError`` alone is not that answer either: it reports the
        one component strict stopped on, never the lexical suffix behind it,
        and a ``..`` in that suffix walks back into existing filesystem where a
        loop can still be waiting. So absence hands off to
        ``_canonicalize_past_absence``, which walks the remainder on its own
        evidence: a not-yet-created workspace target still answers the
        containment question, and unresolvable symlink evidence anywhere in the
        path still raises instead of resolving into a half-answer.
        """
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._workspace_root / candidate
        try:
            return _eloop_as_os_error(lambda: candidate.resolve(strict=True))
        except FileNotFoundError:
            return _canonicalize_past_absence(candidate)

    def _read_like_location_error(self, tool_call: Mapping[str, Any]) -> str | None:
        """Why this read-like prompt carries no proof of a workspace-internal
        target, or ``None`` when every declared location is provably inside.

        A ``read`` grant answers *whether* the agent may read, never *what*, so
        the containment answer has to come from the request. The only path
        authority is the protocol's own ``ToolCallUpdate.locations`` — never
        ``rawInput``, ``_meta``, the title, the content, or any adapter-private
        key, all of which are arbitrary child text. ACP declares
        ``ToolCallLocation.path`` absolute; a relative one is unproven rather
        than leniently workspace-relative, because nothing says which root the
        agent meant. Every reason returned here is ARS-authored, categorical,
        and free of the path it refused.
        """
        locations = tool_call.get("locations")
        if not isinstance(locations, list) or not locations:
            return "read-like permission request has no usable locations"
        for location in locations:
            if not isinstance(location, Mapping):
                return "read-like permission request has an invalid location"
            path = location.get("path")
            if not isinstance(path, str) or not path:
                return "read-like permission request has an invalid location"
            try:
                if not Path(path).is_absolute():
                    return "read-like permission location must be absolute"
                # Same canonical, symlink-resolving containment fs/read_text_file
                # uses, probed strictly because a declared location is evidence
                # rather than an imminent open.
                resolved = self._resolve_read_like_location(path)
            except (OSError, ValueError):
                # A path the platform cannot even parse (an embedded NUL, a
                # component past the system limit) or cannot resolve at all (a
                # symlink loop, an untraversable ancestor) is malformed, not
                # merely uncontained: no containment answer exists for it. Deny
                # categorically — mediation owes a recorded decision rather
                # than an exception escaping into the SDK callback, and a path
                # that never resolved must never reach the inside-check.
                return "read-like permission request has an invalid location"
            if not self._inside_workspace(resolved):
                return "read-like permission location is outside the bound workspace"
        return None

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
            # Unexpected/unmappable prompt: deny and fail the turn. A prompt
            # nobody can answer is not a decision, and must not read as one.
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
            location_error = self._read_like_location_error(tool_call)
            if location_error is not None:
                return self._deny_with_option(
                    requested_op=f"permission:{kind}",
                    reason=location_error,
                    tool_call_id=tool_call_id,
                    options=options,
                )
            return self._allow_once_or_deny(
                kind=kind,
                reason="workspace-scoped read-like operation under read grant",
                tool_call_id=tool_call_id,
                options=options,
            )

        required_capability = _WRITE_FAMILY_REQUIRED_CAPABILITY.get(kind)
        if (
            required_capability is not None
            and required_capability in self._capabilities
        ):
            return self._allow_once_or_deny(
                kind=kind,
                reason=f"{kind} permitted once by the frozen grant",
                tool_call_id=tool_call_id,
                options=options,
            )

        return self._deny_with_option(
            requested_op=f"permission:{kind}",
            reason=f"{kind!r} is not permitted by the frozen grant",
            tool_call_id=tool_call_id,
            options=options,
        )

    # -- completion backstops -------------------------------------------------

    def observe_tool_update(
        self, update: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Watch raw session updates for completions the frozen grant or ARS's
        own decisions contradict. Two families, one event type:

        - ``completed_after_deny``: this exact call was denied by this bridge
          and reported ``completed`` anyway;
        - ``missing_grant_capability``: a write-family tool completed without
          the capability that could ever have allowed it (A4-S2 backstop).

        Kind is correlated by ``toolCallId`` because completed updates may
        omit it (real OpenCode does); for a denied call it falls back to the
        operation ARS itself recorded, never to child free text. Returns a
        durable ``permission_violation`` event dict on detection, else
        ``None``. Both families are detection, never prevention — the tool
        already ran — and a completion that matches neither does not flag: an
        unseen kind cannot be proven write-family, and the mediated ask/deny
        launch binding is the prevention layer.
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
        denied_op = (
            self._denied_tool_calls.get(tool_call_id)
            if isinstance(tool_call_id, str)
            else None
        )
        if denied_op is not None:
            # Checked before the missing-capability family: this call carries a
            # decision ARS itself made, which is the more specific fact, and it
            # covers kinds (read-like, or write-family under a matching grant)
            # that family can never explain.
            if not isinstance(kind, str) and denied_op.startswith("permission:"):
                # ARS's own record of what it was asked for — never child text.
                kind = denied_op.removeprefix("permission:")
            self.grant_violation = True
            reason = "tool completed after ARS denied its permission request"
            self.grant_violation_reason = reason
            return {
                "type": "permission_violation",
                "violation_class": "completed_after_deny",
                "tool_call_id": tool_call_id,
                "kind": kind,
                "reason": reason,
            }
        required = (
            _WRITE_FAMILY_REQUIRED_CAPABILITY.get(kind)
            if isinstance(kind, str)
            else None
        )
        if required is None or required in self._capabilities:
            return None
        self.grant_violation = True
        # ``kind`` is child text and the reason interpolates it; ``required`` is
        # source-owned. Both stay bounded by the event writer's byte ceiling.
        reason = (
            f"write-family tool kind {kind!r} completed without the required "
            f"{required!r} capability in the frozen grant"
        )
        self.grant_violation_reason = reason
        return {
            "type": "permission_violation",
            "violation_class": "missing_grant_capability",
            "tool_call_id": tool_call_id if isinstance(tool_call_id, str) else None,
            "kind": kind,
            "required_capability": required,
            "reason": reason,
        }

    def _allow_once_or_deny(
        self,
        *,
        kind: str,
        reason: str,
        tool_call_id: str | None,
        options: Any,
    ) -> dict[str, Any]:
        """Allow through a once-scoped option, or deny fail-closed."""
        allow_option = _option_id(options, _ALLOW_OPTION_PREFERENCE)
        if allow_option is None:
            return self._deny_with_option(
                requested_op=f"permission:{kind}",
                reason="no once-scoped allow option offered; denying",
                tool_call_id=tool_call_id,
                options=options,
            )
        decision = self._emit(
            requested_op=f"permission:{kind}",
            decision="allow",
            reason=reason,
            tool_call_id=tool_call_id,
        )
        decision["option_id"] = allow_option
        return decision

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
        if isinstance(tool_call_id, str):
            # A caller id is a protocol string, judged by *type* and never by
            # truthiness: ACP constrains ``toolCallId`` to a string, not to a
            # non-empty one, so ``""`` is a present id that correlates like any
            # other. Only a missing or non-string id fails to name a call.
            #
            # ``setdefault``: once ARS denied a call, a later duplicate prompt
            # for the same id cannot erase or rewrite that fact.
            self._denied_tool_calls.setdefault(tool_call_id, requested_op)
        reject_option = _option_id(options, _REJECT_OPTION_PREFERENCE)
        if reject_option is not None:
            decision["option_id"] = reject_option
        return decision


def _option_id(options: Any, preference: tuple[str, ...]) -> str | None:
    """First option matching the *preferred kind order*, not wire order.

    Agents order their own option lists; ARS must not inherit that order for a
    security decision.
    """
    for wanted in preference:
        for option in options or []:
            if isinstance(option, Mapping) and option.get("kind") == wanted:
                option_id = option.get("optionId")
                if isinstance(option_id, str):
                    return option_id
    return None

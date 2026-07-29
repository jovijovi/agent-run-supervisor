from __future__ import annotations

import argparse
import hashlib
import json
import platform
from importlib import resources
import sys
from pathlib import Path
from typing import Any

from agent_run_supervisor.event_store import EventStore
from agent_run_supervisor.goal import GoalPromptError, GoalSpec, compile_goal_prompt
from agent_run_supervisor.mcp_config import McpConfigError
from agent_run_supervisor.parser import ParseResult, parse_acpx_stdout
from agent_run_supervisor.policy import ExecStrategyError
from agent_run_supervisor.preflight import (
    probe_acpx,
    probe_adapter,
    probe_node,
    probe_npx,
    probe_policy,
    probe_redaction,
    probe_session_readiness,
    probe_workspace,
)
from agent_run_supervisor.retention import (
    CleanupPlan,
    CleanupResult,
    RetentionError,
    RetentionPolicy,
    apply_cleanup,
    plan_cleanup,
)
from agent_run_supervisor.role import RoleValidationError, load_role, role_hash
from agent_run_supervisor.runner import SupervisorRunner
from agent_run_supervisor.session import SessionError
from agent_run_supervisor.session_runtime import SessionRuntime, SessionRuntimeError
from agent_run_supervisor.workspace import WorkspaceValidationError

DEFAULT_RUNS_DIR_NAME = Path(".agent-run-supervisor") / "runs"
DEFAULT_SESSIONS_DIR_NAME = Path(".agent-run-supervisor") / "sessions"

def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _parse_role_file(path: str | Path):
    raw = _load_json(path)
    return load_role(raw)


def _parse_result_payload(result: ParseResult) -> dict[str, Any]:
    return {
        "protocol_error": result.protocol_error,
        "protocol_error_reasons": result.protocol_error_reasons,
        "final_message": result.final_message,
        "usage": result.usage,
        "business_verdict": None,
        "truncated": result.truncated,
        "truncate_reason": result.truncate_reason,
        "unknown_update_types": result.unknown_update_types,
        "permission_request_count": result.permission_request_count,
        "permission_denied_count": result.permission_denied_count,
        "event_count": len(result.events),
    }


def cmd_validate_role(args: argparse.Namespace) -> int:
    try:
        role = _parse_role_file(args.role_file)
    except (OSError, json.JSONDecodeError, RoleValidationError) as exc:
        print(f"role validation error: {exc}", file=sys.stderr)
        return 1
    _print_json(
        {
            "valid": True,
            "role_id": role.role_id,
            "role_hash": role_hash(role),
            "allowed_roots_security_boundary": role.workspace.allowed_roots_security_boundary,
        }
    )
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    try:
        result = parse_acpx_stdout(Path(args.events_file))
    except OSError as exc:
        print(f"protocol_error: cannot read events: {exc}", file=sys.stderr)
        return 1
    payload = _parse_result_payload(result)
    if result.protocol_error:
        print("protocol_error: " + "; ".join(result.protocol_error_reasons), file=sys.stderr)
        _print_json(payload)
        return 1
    _print_json(payload)
    return 0


def _default_fixture_dir() -> Path:
    """Return the default fixture directory for the read-only doctor replay.

    Source checkouts keep the full fixture corpus at repository root. Installed
    wheels include the small success fixture needed by ``doctor`` as package
    data, so the console script remains self-contained after non-editable
    installation.
    """
    packaged = resources.files("agent_run_supervisor").joinpath(
        "fixtures", "acpx-0.12.0"
    )
    packaged_replay = packaged.joinpath("success-codex-sentinel", "stdout.ndjson")
    if packaged_replay.is_file():
        return Path(str(packaged))
    return Path(__file__).resolve().parents[2] / "fixtures" / "acpx-0.12.0"


def cmd_doctor(args: argparse.Namespace) -> int:
    role_payload: dict[str, Any] | None = None
    role = None
    if args.role:
        try:
            role = _parse_role_file(args.role)
        except (OSError, json.JSONDecodeError, RoleValidationError) as exc:
            print(f"role validation error: {exc}", file=sys.stderr)
            return 1
        role_payload = {"valid": True, "role_id": role.role_id, "role_hash": role_hash(role)}

    fixtures_dir = Path(args.fixtures) if args.fixtures else _default_fixture_dir()
    replay_path = fixtures_dir / "success-codex-sentinel" / "stdout.ndjson"
    fixture_replay: dict[str, Any]
    if replay_path.exists():
        replay = parse_acpx_stdout(replay_path)
        fixture_replay = _parse_result_payload(replay)
    else:
        fixture_replay = {
            "protocol_error": True,
            "protocol_error_reasons": [f"missing fixture {replay_path}"],
        }

    probe = EventStore(base_dir=Path.cwd() / ".tmp" / "doctor-event-store-probe").permission_probe()
    node_probe = probe_node()
    acpx_probe = probe_acpx(binary=role.runner.acpx_binary if role is not None else None)

    # Always-on, pure-local read-only probes (deterministic in CI).
    redaction_probe = probe_redaction()
    session_probe = probe_session_readiness(role)

    # Role-dependent probes only run when a role is supplied.
    policy_probe = probe_policy(role) if role is not None else None
    workspace_probe = probe_workspace(role) if role is not None else None
    npx_probe = probe_npx(role) if role is not None else None
    adapter_probe = probe_adapter(role) if role is not None else None

    # ``ok`` gates only on pure-local deterministic probes so the no-role CI gate
    # keeps exiting 0 without node/acpx/npx. External-binary probes
    # (node/acpx/npx/adapter) are informational and never flip ``ok``.
    ok = (
        not fixture_replay.get("protocol_error", True)
        and bool(probe.get("dir_mode_ok"))
        and bool(probe.get("file_mode_ok"))
        and bool(redaction_probe.get("ok"))
        and bool(session_probe.get("ok"))
    )
    if role is not None:
        ok = ok and bool(policy_probe.get("ok")) and bool(workspace_probe.get("ok"))

    payload = {
        "ok": ok,
        "python_version": platform.python_version(),
        "node_version_requirement": ">=22.12",
        "launched_real_agent": False,
        "event_store_probe": probe,
        "fixture_replay": fixture_replay,
        "role_validation": role_payload,
        "node_probe": node_probe,
        "acpx_probe": acpx_probe,
        "redaction_probe": redaction_probe,
        "session_probe": session_probe,
        "policy_probe": policy_probe,
        "workspace_probe": workspace_probe,
        "npx_probe": npx_probe,
        "adapter_probe": adapter_probe,
    }
    _print_json(payload)
    return 0 if payload["ok"] else 1


def cmd_run(args: argparse.Namespace) -> int:
    try:
        role = _parse_role_file(args.role)
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError, RoleValidationError) as exc:
        print(f"run input error: {exc}", file=sys.stderr)
        return 1
    runner = SupervisorRunner(runs_dir=Path(args.runs_dir) if args.runs_dir else None)
    try:
        if args.no_real_run:
            outcome = runner.dry_run(role=role, prompt=prompt, cwd=args.cwd)
            _print_json(outcome.result)
            return 0
        outcome = runner.run(role=role, prompt=prompt, cwd=args.cwd)
    except ExecStrategyError as exc:
        print(f"session strategy error: {exc}", file=sys.stderr)
        return 1
    except WorkspaceValidationError as exc:
        print(f"workspace validation error: {exc}", file=sys.stderr)
        return 1
    except McpConfigError as exc:
        print(f"mcp config error: {exc}", file=sys.stderr)
        return 1
    _print_json(outcome.result)
    return 0 if outcome.result["status"] == "completed" else 1


def _resolve_sessions_dir(args: argparse.Namespace) -> Path:
    return (
        Path(args.sessions_dir)
        if args.sessions_dir
        else Path.cwd() / DEFAULT_SESSIONS_DIR_NAME
    )


def _cmd_session_list(args: argparse.Namespace, sessions_dir: Path) -> int:
    role = None
    if getattr(args, "role", None):
        try:
            role = _parse_role_file(args.role)
        except (OSError, json.JSONDecodeError, RoleValidationError) as exc:
            print(f"session input error: {exc}", file=sys.stderr)
            return 1
    runtime = SessionRuntime(sessions_dir=sessions_dir)
    outcome = runtime.list_sessions(role=role)
    _print_json(outcome.result)
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    session_command = getattr(args, "session_command", None)
    if session_command is None:
        print(
            "error: a session subcommand is required "
            "(create | send | status | close | abort | list)",
            file=sys.stderr,
        )
        return 2

    sessions_dir = _resolve_sessions_dir(args)

    # ``list`` is local, read-only, and role-optional: handle it before the
    # role-required subcommands so it never requires a role file.
    if session_command == "list":
        return _cmd_session_list(args, sessions_dir)

    try:
        role = _parse_role_file(args.role)
    except (OSError, json.JSONDecodeError, RoleValidationError) as exc:
        print(f"session input error: {exc}", file=sys.stderr)
        return 1

    runtime = SessionRuntime(sessions_dir=sessions_dir)

    if session_command == "send":
        goal_file = getattr(args, "goal_file", None)
        try:
            if goal_file:
                # Fail closed on unsafe goal text BEFORE any lease/acpx work.
                # Compile the goal onto what the role's adapter can actually
                # execute: adapters without a fixture-proven native ACP `goal`
                # command (all of them today) get the goal-contract/v1 text
                # template — a literal "/goal" slash turn is a proven
                # transport-completed no-op on the codex ACP surface.
                compiled = compile_goal_prompt(
                    role,
                    GoalSpec(
                        goal_text=Path(goal_file).read_text(encoding="utf-8")
                    ),
                )
                prompt = compiled.prompt
            else:
                prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"session input error: {exc}", file=sys.stderr)
            return 1
        except GoalPromptError as exc:
            print(f"goal input error: {exc}", file=sys.stderr)
            return 1

    try:
        if session_command == "create":
            outcome = runtime.create_session(
                role=role,
                session_id=args.session_id,
                session_name=args.session_name,
                cwd=args.cwd,
            )
            _print_json(outcome.result)
            return 0
        if session_command == "send":
            turn = runtime.send(
                role=role,
                session_id=args.session_id,
                prompt=prompt,
                cwd=args.cwd,
            )
            _print_json(turn.result)
            return 0 if turn.result["status"] == "completed" else 1
        if session_command == "status":
            status = runtime.status(
                role=role,
                session_id=args.session_id,
                cwd=args.cwd,
            )
            _print_json(status.result)
            return 0 if status.ok else 1
        if session_command == "close":
            closed = runtime.close(
                role=role,
                session_id=args.session_id,
                cwd=args.cwd,
            )
            _print_json(closed.result)
            return 0
        if session_command == "abort":
            aborted = runtime.abort(
                role=role,
                session_id=args.session_id,
                cwd=args.cwd,
            )
            _print_json(aborted.result)
            return 0
    except ExecStrategyError as exc:
        print(f"session strategy error: {exc}", file=sys.stderr)
        return 1
    except WorkspaceValidationError as exc:
        print(f"workspace validation error: {exc}", file=sys.stderr)
        return 1
    except McpConfigError as exc:
        print(f"mcp config error: {exc}", file=sys.stderr)
        return 1
    except SessionRuntimeError as exc:
        print(f"session runtime error: {exc}", file=sys.stderr)
        return 1
    except SessionError as exc:
        print(f"session error: {exc}", file=sys.stderr)
        return 1

    print(f"error: unknown session subcommand {session_command!r}", file=sys.stderr)
    return 2


def _candidate_payload(candidate: Any) -> dict[str, Any]:
    return {
        "kind": candidate.kind,
        "id": candidate.id,
        "path": str(candidate.path),
        "age_seconds": candidate.age_seconds,
        "action": candidate.action,
        "reason": candidate.reason,
    }


def _plan_payload(plan: CleanupPlan) -> dict[str, Any]:
    return {
        "root": str(plan.root),
        "runs_dir": str(plan.runs_dir),
        "sessions_dir": str(plan.sessions_dir),
        "delete": [_candidate_payload(c) for c in plan.delete],
        "skip": [_candidate_payload(c) for c in plan.skip],
    }


def cmd_cleanup(args: argparse.Namespace) -> int:
    runs_dir = Path(args.runs_dir) if args.runs_dir else Path.cwd() / DEFAULT_RUNS_DIR_NAME
    sessions_dir = (
        Path(args.sessions_dir)
        if args.sessions_dir
        else Path.cwd() / DEFAULT_SESSIONS_DIR_NAME
    )
    policy = RetentionPolicy(max_age_days=args.max_age_days, max_count=args.max_count)
    try:
        plan = plan_cleanup(
            runs_dir=runs_dir,
            sessions_dir=sessions_dir,
            policy=policy,
        )
    except RetentionError as exc:
        print(f"retention error: {exc}", file=sys.stderr)
        return 1

    if not args.apply:
        # Dry-run is the default: list first, delete nothing.
        _print_json({"applied": False, "plan": _plan_payload(plan)})
        return 0

    result: CleanupResult = apply_cleanup(plan, confirm=True)
    _print_json(
        {
            "applied": True,
            "deleted": result.deleted,
            "failed": result.failed,
            "plan": _plan_payload(plan),
        }
    )
    return 0 if not result.failed else 1


# --- Runtime Binding operator surface (PRD R13, C13/C14) ---------------------


def _binding_ownership(args: argparse.Namespace):
    """The operator's declared artifact-trust policy for this invocation.

    Defaults are fail-closed: only root may own the artifact root, and this
    process's effective UID is treated as the ``arsd``/AGENT UID that must not
    be able to rewrite it. An operator whose root is owned by a dedicated
    non-service account states that explicitly rather than having it inferred.
    """
    import os

    from agent_run_supervisor.native_acp import runtime_binding as rb

    trusted = frozenset(args.trusted_uid) if args.trusted_uid else frozenset({0})
    service = args.service_uid if args.service_uid is not None else os.geteuid()
    return rb.TrustedOwnership(trusted_uids=trusted, service_uid=service)


def _resolve_binding_profile(profile_id: str):
    from agent_run_supervisor.native_acp.profile import (
        DEFAULT_REGISTRY,
        UnknownProfileError,
    )

    try:
        return DEFAULT_REGISTRY.get(profile_id)
    except UnknownProfileError:
        return None


def _unknown_profile_report(profile_id: str) -> dict[str, Any]:
    return {"valid": False, "rule": "UNKNOWN_PROFILE", "profile_id": profile_id}


def _validated_generation(
    args: argparse.Namespace, profile: Any
) -> tuple[int, dict[str, Any], Any]:
    """Shared validate/promote/rollback core: full validation plus the probe.

    Returns the probed :class:`ResolvedBinding` itself, because C6 makes the
    probed object the only object a promotion may activate.
    """
    from agent_run_supervisor.native_acp import runtime_binding as rb

    agent_id = getattr(args, "agent", None)
    try:
        resolved = rb.validate_generation(
            Path(args.binding_root),
            args.generation,
            profile=profile,
            ownership=_binding_ownership(args),
            agent_id=agent_id,
            probe=True,
        )
    except rb.BindingRefusal as refusal:
        return 1, {
            "valid": False,
            "rule": refusal.rule,
            "profile_id": profile.profile_id,
            "agent_id": agent_id,
            "generation_id": args.generation,
        }, None
    report: dict[str, Any] = {
        "valid": True,
        "profile_id": profile.profile_id,
        "agent_id": agent_id,
        "profile_revision": profile.revision,
        "adapter_contract_hash": profile.adapter_contract_hash(),
        "generation_id": resolved.generation_id,
        "manifest_sha256": resolved.manifest_sha256,
        "generation_hash": resolved.generation_hash,
        "slot_set_hash": resolved.slot_set_hash,
        "session_compatibility_epoch": resolved.session_compatibility_epoch,
        "acceptance_receipt_ref": resolved.acceptance_receipt_ref,
        "declared_version": None,
        "probe_version": None,
    }
    if agent_id is not None:
        report["agent_registration_hash"] = resolved.contract_identity.get(
            "agent_registration_hash"
        )
    if profile.contract.cli_slot is not None:
        report["declared_version"] = rb.declared_cli_version(resolved, profile)
        # ``validate_generation`` already probed and matched this exact object;
        # the declared value is reported rather than the CLI re-run, so the
        # report can never describe a different probe than the one that gated.
        report["probe_version"] = report["declared_version"]
    return 0, report, resolved


def cmd_runtime_binding(args: argparse.Namespace) -> int:
    command = getattr(args, "runtime_binding_command", None)
    if command is None:
        print(
            "error: a runtime-binding subcommand is required "
            "(validate | promote | rollback | inspect-run)",
            file=sys.stderr,
        )
        return 2
    if command == "inspect-run":
        return _cmd_binding_inspect_run(args)
    if command in ("validate", "promote", "rollback"):
        return _cmd_binding_generation(args, command)
    print(f"error: unknown runtime-binding subcommand {command!r}", file=sys.stderr)
    return 2


def _cmd_binding_generation(args: argparse.Namespace, command: str) -> int:
    from agent_run_supervisor.native_acp import runtime_binding as rb

    # Every Binding path this command touches is scoped to one registered
    # profile, so the profile is resolved from the closed registry before any
    # root is opened. A promotion for one profile never reads or replaces
    # another's active selection.
    profile = _resolve_binding_profile(args.profile)
    if profile is None:
        _print_json(_unknown_profile_report(args.profile))
        return 1

    if command == "rollback":
        # A rollback targets a generation other than the active one: rolling
        # back to what is already promoted would be a silent no-op that reads
        # like a successful recovery. The comparison is scoped to the same
        # agent, so one agent's active selection never grades another's.
        try:
            active = rb.read_active_pointer(
                Path(args.binding_root),
                profile=profile,
                ownership=_binding_ownership(args),
                agent_id=getattr(args, "agent", None),
            )
        except rb.BindingRefusal as refusal:
            _print_json({"valid": False, "rule": refusal.rule})
            return 1
        if active is not None and active[0] == args.generation:
            _print_json(
                {
                    "valid": False,
                    "rule": "ALREADY_ACTIVE",
                    "generation_id": args.generation,
                }
            )
            return 1

    code, report, resolved = _validated_generation(args, profile)
    if code != 0 or command == "validate":
        _print_json(report)
        return code

    # C6: promote exactly the object the probe proved. A second read here could
    # activate a manifest that replaced it mid-command, so there is no second
    # read — ``validate_generation`` has already re-confirmed that this
    # generation's bytes did not change while it was being probed.
    try:
        rb.write_active_pointer(
            Path(args.binding_root),
            resolved,
            profile=profile,
            ownership=_binding_ownership(args),
            agent_id=getattr(args, "agent", None),
        )
    except rb.BindingRefusal as refusal:
        _print_json({"valid": True, "promoted": False, "rule": refusal.rule})
        return 1
    except OSError as exc:
        _print_json({"valid": True, "promoted": False, "error": exc.__class__.__name__})
        return 1
    report["promoted"] = True
    if command == "rollback":
        report["rolled_back_to"] = resolved.generation_id
    _print_json(report)
    return 0


# The one top-level field the launch hash excludes — and the only one. The
# inspector pops it from a copy, so a second exclusion is not expressible.
LAUNCH_SEAL_FIELD = "launch_spec_hash"


def _recompute_launch_hash(record: dict[str, Any]) -> str:
    body = dict(record)
    body.pop(LAUNCH_SEAL_FIELD, None)
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cmd_binding_inspect_run(args: argparse.Namespace) -> int:
    """C13: recompute one Run's launch seal and report its provenance.

    Reads ``launch.json`` and ``spec.json`` and writes nothing. A pre-PR-B
    record carries no runtime provenance and possibly no embedded seal; it is
    reported as a legacy launch record and verified against ``spec.json``
    alone rather than failing.
    """
    run_dir = Path(args.run_dir)
    try:
        record = json.loads((run_dir / "launch.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _print_json({"error": "LAUNCH_RECORD_MISSING", "run_dir": str(run_dir)})
        return 1
    if not isinstance(record, dict):
        _print_json({"error": "LAUNCH_RECORD_MALFORMED", "run_dir": str(run_dir)})
        return 1

    spec_launch_hash = None
    try:
        spec = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
        if isinstance(spec, dict):
            spec_launch_hash = spec.get(LAUNCH_SEAL_FIELD)
    except (OSError, json.JSONDecodeError):
        spec_launch_hash = None

    recomputed = _recompute_launch_hash(record)
    embedded = record.get(LAUNCH_SEAL_FIELD)
    provenance = record.get("runtime_provenance")
    sealed = record.get("expected_runtime") or {}
    cli = sealed.get("cli") or {}
    legacy = provenance is None

    report: dict[str, Any] = {
        "run_dir": str(run_dir),
        "legacy_launch_record": legacy,
        "recomputed_launch_spec_hash": recomputed,
        "embedded_seal": embedded,
        "spec_launch_spec_hash": spec_launch_hash,
        "seal_verified": embedded is not None and embedded == recomputed,
        "matches_spec": spec_launch_hash is not None and spec_launch_hash == recomputed,
        "profile_id": record.get("profile_id"),
        "profile_revision": record.get("profile_revision"),
        "profile_hash": record.get("profile_hash"),
        "config_schema_hash": record.get("config_schema_hash"),
        "adapter_contract_hash": (provenance or {}).get("adapter_contract_hash"),
        "launch_kind": sealed.get("launch_kind") or (provenance or {}).get("launch_kind"),
        "agent_info_name": sealed.get("agent_info_name"),
        "agent_info_version": sealed.get("agent_info_version"),
        "protocol_version": sealed.get("protocol_version"),
        "interpreter": {
            "path": sealed.get("node_path"),
            "sha256": sealed.get("node_sha256"),
        },
        "adapter_entry": {
            "path": sealed.get("adapter_entry_path"),
            "sha256": sealed.get("adapter_entry_sha256"),
        },
        "cli": dict(cli) if cli else None,
        "binding": None,
        "session_compatibility_epoch": (provenance or {}).get(
            "session_compatibility_epoch"
        ),
    }
    if provenance is not None:
        report["binding"] = {
            "generation_id": provenance.get("generation_id"),
            "manifest_sha256": provenance.get("manifest_sha256"),
            "generation_hash": provenance.get("generation_hash"),
            "slot_set_hash": provenance.get("slot_set_hash"),
            "slot_hashes": provenance.get("slot_hashes"),
            # Recorded and reported, never an authorization input.
            "acceptance_receipt_ref": provenance.get("acceptance_receipt_ref"),
            "acceptance_receipt_sha256": provenance.get("acceptance_receipt_sha256"),
        }
    _print_json(report)
    if legacy:
        # A pre-PR-B record carries no embedded seal, so it is graded against
        # ``spec.json`` alone — graded, not waived. Reporting a legacy record
        # whose launch hash disagrees with the Run's sealed spec as a success
        # would make forged legacy evidence indistinguishable from intact
        # evidence, and a missing spec leaves nothing to verify against.
        return 0 if report["matches_spec"] else 1
    return 0 if report["seal_verified"] and report["matches_spec"] else 1

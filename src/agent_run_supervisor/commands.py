from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from agent_run_supervisor.managed_process import (
    ManagedProcessError,
    ManagedProcessLimits,
    spawn_managed_process,
)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


# --- Agent registry operator surface -----------------------------------------


def cmd_agents(args: argparse.Namespace) -> int:
    command = getattr(args, "agents_command", None)
    if command is None:
        print(
            "error: an agents subcommand is required (validate | doctor)",
            file=sys.stderr,
        )
        return 2
    if command == "validate":
        return _cmd_agents_validate(args)
    if command == "doctor":
        return _cmd_agents_doctor(args)
    print(f"error: unknown agents subcommand {command!r}", file=sys.stderr)
    return 2


def _load_registry(agents_file: str):
    """Parse the agents file exactly as the daemon does, or report the rule.

    One function, so ``agents validate``, ``agents doctor``, and the daemon
    cannot drift into three readings of the same file.
    """
    from agent_run_supervisor.native_acp import agent_registry

    try:
        return agent_registry.load_agents_file(agents_file), None
    except agent_registry.RegistryRefusal as refusal:
        return None, {"valid": False, "rule": refusal.rule, "message": refusal.message}


def _cmd_agents_validate(args: argparse.Namespace) -> int:
    """Shape, bounds, and the identical startup mediation-collision check.

    Output is value-blind by construction: entry ids, counts, environment
    **names**, source classes, and rule outcomes. An overlay value and a
    mediation value are never printed, and a refusal names the failing rule and
    at most a field path or an environment name.
    """
    from agent_run_supervisor.native_acp.agent_registration import entry_projection

    snapshot, refusal = _load_registry(args.agents_file)
    if snapshot is None:
        _print_json(refusal)
        return 1
    _print_json(
        {
            "valid": True,
            "agents_file": str(args.agents_file),
            "agent_count": len(snapshot),
            "agents": [entry_projection(snapshot.get(name)) for name in snapshot.ids()],
        }
    )
    return 0


# The bound on one zero-prompt ``initialize``. A diagnostic that can hang is not
# a diagnostic, and the child is reaped on this path exactly as on every other.
PROBE_TIMEOUT_SECONDS = 30.0
# Every cleanup step is bounded too, because "cleanup" that can block forever is
# just a leak with a reassuring name. Close, then TERM and reap, then — only if
# the group is still there — KILL and reap.
PROBE_CLOSE_TIMEOUT_SECONDS = 5.0
PROBE_TERM_GRACE_SECONDS = 5.0
PROBE_KILL_GRACE_SECONDS = 5.0
# How often the launched group is re-checked for absence inside those bounds.
PROBE_GROUP_POLL_SECONDS = 0.02

# Stable categorical probe outcomes. Nothing else is ever reported: a probe
# result carries no agent self-report, no stderr, no exception text, no external
# session id, and no other child-controlled free text.
PROBE_OK = "ok"
PROBE_REFUSED = "refused"
PROBE_FAILED = "failed"
PROBE_TIMEOUT = "PROBE_TIMEOUT"
PROBE_FAILED_CODE = "PROBE_FAILED"
PROBE_CLEANUP_FAILED = "PROBE_CLEANUP_FAILED"
ACP_SDK_UNAVAILABLE = "ACP_SDK_UNAVAILABLE"


def _probe_report(
    outcome: str,
    *,
    code: str | None = None,
    summary: Any = None,
    instance: Any = None,
) -> dict[str, Any]:
    """The complete, closed set of facts a probe may report.

    Deliberately excludes the agent's self-reported name and version. Those are
    child-controlled free text, they are evidence rather than identity, and a
    diagnostic that echoes them hands an operator back whatever the child chose
    to say. What survives is structural — an integer, two booleans, a count —
    plus the *source-owned* capability names the profile itself declared, so no
    child-supplied string reaches the report at all.
    """
    report: dict[str, Any] = {
        "outcome": outcome,
        "code": code,
        "protocol_version": None,
        "load_session_advertised": None,
        "advertised_capability_count": None,
        "required_capabilities_present": None,
        "forbidden_capabilities_present": None,
    }
    if summary is None or instance is None:
        return report
    advertised = summary.capabilities or {}
    report["protocol_version"] = summary.protocol_version
    report["load_session_advertised"] = bool(summary.load_session_advertised)
    report["advertised_capability_count"] = len(advertised)
    report["required_capabilities_present"] = [
        name for name in instance.required_capabilities if advertised.get(name)
    ]
    report["forbidden_capabilities_present"] = [
        name for name in instance.forbidden_capabilities if advertised.get(name)
    ]
    return report


def _probe_client(instance: Any) -> Any:
    """Build the SDK-backed driver. Raises **before** anything is started.

    Separated from the spawn for one reason: ``NativeAcpClient.__init__`` calls
    ``require_sdk()``. Constructing it after the child exists means an
    environment without the optional SDK fails with a live agent already
    running and nothing left holding a reference to it. Setup that can refuse
    therefore happens while there is still nothing to leak.
    """
    from agent_run_supervisor.native_acp.client import NativeAcpClient
    from agent_run_supervisor.native_acp.config_fidelity import (
        EFFORT_NOT_APPLICABLE,
        FIDELITY_MODEL_ONLY,
        ConfigFidelityMachine,
    )
    from agent_run_supervisor.native_acp.driver import NativeAcpDriver

    # The probe reaches ``initialize`` and stops, so the machine is never
    # driven — but it still has to be constructible under the instance's own
    # fidelity mode rather than under an assumed one.
    model_only = instance.config_fidelity_mode == FIDELITY_MODEL_ONLY
    return NativeAcpDriver(
        client=NativeAcpClient(on_update=lambda _update: None),
        machine=ConfigFidelityMachine(
            model_selector_id=instance.model_selector_id,
            effort_selector_id=instance.effort_selector_id,
            requested_model="",
            requested_effort=EFFORT_NOT_APPLICABLE if model_only else "",
            fidelity_mode=instance.config_fidelity_mode,
        ),
    )


async def _reaped_within(proc: Any, timeout: float) -> bool:
    """Await leader exit within the bound. False means it is still running."""
    try:
        await asyncio.wait_for(proc.wait(), timeout)
        return True
    except Exception:
        return False


def _group_is_gone(proc: Any) -> bool:
    """Is the launched group empty? An unanswerable probe means "no"."""
    try:
        return bool(proc.group_is_gone())
    except Exception:
        return False


async def _group_settled_within(proc: Any, timeout: float) -> bool:
    """Leader reaped **and** its process group empty, inside one bound.

    Both halves, because they are different facts. ``wait()`` reaps the direct
    leader; a descendant that inherited the group is not the leader, does not
    get reaped by it, and is not reported by it. The sharp case is a descendant
    that ignores SIGTERM *and* releases the inherited pipes: stderr reaches EOF
    the instant the leader exits, so the reap returns immediately and looks
    perfect while the descendant keeps running.

    The group is polled rather than probed once, because the descendants
    signalled alongside the leader normally need a moment to go.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    if not await _reaped_within(proc, max(deadline - loop.time(), 0.0)):
        return False
    while True:
        if _group_is_gone(proc):
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(PROBE_GROUP_POLL_SECONDS)


async def _probe_cleanup(driver: Any, proc: Any) -> str | None:
    """Close and reap, bounded at every step. ``None`` means the group is gone.

    One helper rather than a finally-fragment per exit path, because the
    property that matters — *no probe returns with the group alive* — is a
    property of all the paths together, and duplicated fragments drift.

    The escalation is not optional politeness: an agent that ignores SIGTERM,
    or whose ``close`` hangs, is exactly the case where a diagnostic silently
    leaves a process behind. When even SIGKILL plus a bounded reap does not
    settle it, the caller is told so rather than handed a clean-looking report.
    ``driver`` may be ``None``: setup can fail after the spawn, and the group is
    still ours to reap.
    """
    if driver is not None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(driver.close(), PROBE_CLOSE_TIMEOUT_SECONDS)
    with contextlib.suppress(Exception):
        proc.terminate_group(reason="agents_doctor_probe_complete")
    if await _group_settled_within(proc, PROBE_TERM_GRACE_SECONDS):
        return None
    with contextlib.suppress(Exception):
        proc.kill_group(reason="agents_doctor_probe_escalation")
    if await _group_settled_within(proc, PROBE_KILL_GRACE_SECONDS):
        return None
    return PROBE_CLEANUP_FAILED


async def _probe_exchange(driver: Any, proc: Any, instance: Any) -> dict[str, Any]:
    """One zero-prompt ``initialize``, judged. Starts nothing and reaps nothing.

    No ``session/new``, no ``session/load``, no prompt, and no prompt text is
    ever constructed. Cleanup is the caller's, so there is exactly one place
    where the child is reaped.
    """
    from agent_run_supervisor.native_acp.observation import (
        InitializeObservation,
        judge_initialize,
    )

    try:
        await driver.open(proc)
        summary = await asyncio.wait_for(driver.initialize(), PROBE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return _probe_report(PROBE_FAILED, code=PROBE_TIMEOUT)
    except Exception:
        # Every driver/SDK failure collapses to one categorical code: the SDK's
        # message routinely renders rejected wire values.
        return _probe_report(PROBE_FAILED, code=PROBE_FAILED_CODE)
    verdict = judge_initialize(
        instance,
        InitializeObservation(
            agent_info=summary.agent_info,
            protocol_version=summary.protocol_version,
            capabilities=summary.capabilities,
            load_session_advertised=summary.load_session_advertised,
        ),
    )
    outcome = PROBE_OK if verdict.refusal is None else PROBE_REFUSED
    return _probe_report(
        outcome, code=verdict.refusal, summary=summary, instance=instance
    )


async def _probe_initialize(instance: Any, resolved: Any) -> dict[str, Any]:
    """Start the registered command and speak ACP exactly once.

    Order is what keeps the probe honest: setup that can refuse runs before the
    spawn, and from the spawn onward every path — success, refusal, timeout,
    unexpected failure — leaves through the same bounded cleanup. Every failure
    the probe can report is a stable code, never SDK or OS text.
    """
    from agent_run_supervisor.native_acp import NativeSdkUnavailableError

    try:
        driver = _probe_client(instance)
    except NativeSdkUnavailableError:
        return _probe_report(PROBE_FAILED, code=ACP_SDK_UNAVAILABLE)
    except Exception:
        return _probe_report(PROBE_FAILED, code=PROBE_FAILED_CODE)

    try:
        proc = await spawn_managed_process(
            argv=list(instance.argv),
            cwd=Path.cwd(),
            env=resolved,
            limits=ManagedProcessLimits(),
        )
    except ManagedProcessError as exc:
        # Classified from the errno ARS observed. The exception's own text
        # names the declared image path and never reaches the report.
        return _probe_report(PROBE_FAILED, code=exc.code)
    except Exception:
        return _probe_report(PROBE_FAILED, code=PROBE_FAILED_CODE)

    try:
        report = await _probe_exchange(driver, proc, instance)
    except BaseException:
        # Nothing between the spawn and the cleanup may escape, including a
        # cancellation: the group would outlive the command that started it.
        await _probe_cleanup(driver, proc)
        raise
    cleanup = await _probe_cleanup(driver, proc)
    if cleanup is not None:
        return _probe_report(PROBE_FAILED, code=cleanup)
    return report


def _cmd_agents_doctor(args: argparse.Namespace) -> int:
    """The projected environment **name** set, per agent, plus the declared launch.

    ``PATH`` is the single most likely cause of "works in my shell, fails under
    ARS", so the projection is reported by name rather than left mysterious. No
    value appears: the report is built from the same value-blind projection the
    launch snapshot carries.

    Without ``--no-probe`` this command starts the registered external command.
    "Read-only" refers to ARS and operator state and never claims otherwise
    about the child, which writes its own AGENT-owned state.
    """
    import os

    from agent_run_supervisor.native_acp import agent_registry
    from agent_run_supervisor.native_acp.profile import DEFAULT_REGISTRY
    from agent_run_supervisor.native_acp.spec import resolve_run_environment

    snapshot, refusal = _load_registry(args.agents_file)
    if snapshot is None:
        _print_json(refusal)
        return 1
    requested = getattr(args, "agent", None)
    if requested is None:
        agent_ids = list(snapshot.ids())
    else:
        try:
            snapshot.get(requested)
        except agent_registry.RegistryRefusal as exc:
            _print_json({"valid": False, "rule": exc.rule, "message": exc.message})
            return 1
        agent_ids = [requested]

    from agent_run_supervisor.native_acp.profile import AgentInstance

    no_probe = bool(getattr(args, "no_probe", False))
    reports: list[dict[str, Any]] = []
    healthy = True
    for agent_id in agent_ids:
        entry = snapshot.get(agent_id)
        profile = DEFAULT_REGISTRY.get(entry.profile_id)
        resolved = resolve_run_environment(
            arsd_env=dict(os.environ), profile=profile, entry=entry
        )
        probe: dict[str, Any] | None = None
        if not no_probe:
            probe = asyncio.run(
                _probe_initialize(AgentInstance(profile, entry), resolved)
            )
            healthy = healthy and probe["outcome"] == PROBE_OK
        reports.append(
            {
                "agent_id": agent_id,
                "profile": entry.profile_id,
                "command": entry.command,
                "argv": list(entry.argv()),
                "mediation": entry.mediation_id,
                "session_epoch": entry.session_epoch,
                "env": resolved.value_blind_projection().to_dict(),
                "probe": probe,
            }
        )
    _print_json({"valid": True, "agents": reports})
    return 0 if healthy else 1


# --- run inspect --------------------------------------------------------------

# The one top-level field the launch hash excludes — and the only one. The
# inspector pops it from a copy, so a second exclusion is not expressible.
LAUNCH_SEAL_FIELD = "launch_spec_hash"

# The categorical marker every withheld legacy free-form field collapses to.
LEGACY_TEXT_EVIDENCE_WITHHELD = "LEGACY_TEXT_EVIDENCE_WITHHELD"
LEGACY_SEAL_VERIFICATION = "not_performed_value_bearing_legacy"


def _recompute_launch_hash(record: dict[str, Any]) -> str:
    body = dict(record)
    body.pop(LAUNCH_SEAL_FIELD, None)
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _classify_launch_schema(record: dict[str, Any]) -> str:
    """Decide which *kind* of record this is, before any verifier is selected.

    Classification first is the whole point. Selecting the verifier and
    discovering the schema afterwards is how a value ends up inside a digest ARS
    then publishes.

    "Reset-schema" therefore means **exactly** the production launch
    projection — the same closed allowlist the writer emits, checked by the same
    function. A looser test (current ``schema_version`` plus a dict ``env``)
    admits the one shape that actually matters: a *hybrid* that looks new enough
    to pass while still carrying a surviving value-bearing key such as
    ``fixed_env``. That record would reach the launch hash, and the value would
    become digest material in a report ARS prints.

    Anything that is not exactly the production projection — a key added, a key
    missing, or an ``env`` block that is not the value-blind shape — is a
    value-bearing record and goes to the withholding path.
    """
    from agent_run_supervisor.native_acp.spec import launch_payload_shape_is_exact

    return "reset" if launch_payload_shape_is_exact(record) else "legacy"


def _inspect_reset_record(run_dir: Path, record: dict[str, Any]) -> tuple[int, dict]:
    """Recompute the value-blind launch hash and report allowlisted evidence."""
    from agent_run_supervisor.native_acp.spec import env_projection_shape_is_exact

    recomputed = _recompute_launch_hash(record)
    embedded = record.get(LAUNCH_SEAL_FIELD)
    env = record.get("env") or {}
    report = {
        "run_dir": str(run_dir),
        "schema": "reset",
        "legacy_value_bearing": False,
        "environment_values_withheld": True,
        "recomputed_launch_spec_hash": recomputed,
        "embedded_seal": embedded,
        "seal_verified": embedded is not None and embedded == recomputed,
        "agent_id": record.get("agent_id"),
        "profile_id": record.get("profile_id"),
        "profile_revision": record.get("profile_revision"),
        "profile_hash": record.get("profile_hash"),
        "command": record.get("command"),
        "argv": record.get("argv"),
        "mediation_id": record.get("mediation_id"),
        "session_epoch": record.get("session_epoch"),
        "env": env if env_projection_shape_is_exact(env) else None,
    }
    return (0 if report["seal_verified"] else 1), report


def _inspect_legacy_record(run_dir: Path, record: dict[str, Any]) -> tuple[int, dict]:
    """Report a pre-reset record through a categorical allowlist, and nothing else.

    Every value-bearing surface of the retired launch schema is withheld:
    ``fixed_env``/``permission_env``/``env_allowlist``, the sealed runtime and
    its provenance, the embedded seal itself, and any free-form text. Only
    structural facts survive.

    No hash is recomputed here — not over the record, not over a subset of it.
    Recomputing one would take an environment value as digest input and then
    publish the result, which is exactly what "no value, and no metadata
    computed to represent one" forbids.

    ``profile_id`` and ``profile_revision`` are withheld with everything else.
    They *look* structural, and that is the trap: in a legacy record they are
    free-form bytes from a document ARS did not write under the reset contract,
    so an environment value sits in either one as easily as in ``fixed_env``.
    Copying them through would have made the withholding path the leak. What
    survives is the categorical fact that the record carried them.
    """
    report = {
        "run_dir": str(run_dir),
        "schema": "legacy",
        "legacy_value_bearing": True,
        "environment_values_withheld": True,
        "launch_seal_verification": LEGACY_SEAL_VERIFICATION,
        "text_evidence": LEGACY_TEXT_EVIDENCE_WITHHELD,
        "profile_identity": (
            LEGACY_TEXT_EVIDENCE_WITHHELD
            if any(key in record for key in ("profile_id", "profile_revision"))
            else None
        ),
        "argv_token_count": (
            len(record["argv"]) if isinstance(record.get("argv"), list) else None
        ),
    }
    return 0, report


def cmd_run_inspect(args: argparse.Namespace) -> int:
    """Per-Run launch evidence. Reads ``launch.json`` and writes nothing.

    The schema is classified **before** a verifier is selected, so a
    value-bearing legacy record can never reach the reset-schema path.
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

    if _classify_launch_schema(record) == "reset":
        code, report = _inspect_reset_record(run_dir, record)
    else:
        code, report = _inspect_legacy_record(run_dir, record)
    _print_json(report)
    return code

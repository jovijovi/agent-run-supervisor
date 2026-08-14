"""Small shared primitives for the two fixed quick-health controllers."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from agent_run_supervisor.process_liveness import CRASHED, classify_holder


TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "timed_out", "unknown"}
)
REQUIRED_OPERATIONS = frozenset(
    {"server_info", "submit", "run_status", "run_events"}
)
_AGENT_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class ControllerError(Exception):
    """Local failure with a stable, shareable category."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class ConfigurationError(ControllerError):
    pass


class PreflightError(ControllerError):
    pass


class EvidenceError(ControllerError):
    pass


class CaseFailure(ControllerError):
    pass


@dataclass(frozen=True)
class AgentRoute:
    agent_id: str
    model: str
    effort: str

    def __post_init__(self) -> None:
        if _AGENT_ID.fullmatch(self.agent_id) is None:
            raise ConfigurationError("AGENT_ROUTE")
        for value in (self.model, self.effort):
            if not isinstance(value, str) or not value or not value.isprintable():
                raise ConfigurationError("AGENT_ROUTE")


@dataclass(frozen=True)
class RequestLimits:
    """Controller-selected ARS request limits, not deployment defaults."""

    startup_timeout_seconds: float = 120.0
    turn_timeout_seconds: float = 600.0
    cancel_grace_seconds: float = 10.0
    max_stderr_bytes: int = 1_048_576
    max_event_bytes: int = 131_072
    max_events: int = 8_000

    def __post_init__(self) -> None:
        for value in (
            self.startup_timeout_seconds,
            self.turn_timeout_seconds,
            self.cancel_grace_seconds,
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ConfigurationError("REQUEST_LIMITS")
        for value in (self.max_stderr_bytes, self.max_event_bytes, self.max_events):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ConfigurationError("REQUEST_LIMITS")

    def to_wire(self) -> dict[str, int | float]:
        return {
            "startup_timeout_seconds": self.startup_timeout_seconds,
            "turn_timeout_seconds": self.turn_timeout_seconds,
            "cancel_grace_seconds": self.cancel_grace_seconds,
            "max_stderr_bytes": self.max_stderr_bytes,
            "max_event_bytes": self.max_event_bytes,
            "max_events": self.max_events,
        }


@dataclass(frozen=True)
class ControllerPolicy:
    """Local polling/deadline policy, independent of ARS Run limits."""

    poll_seconds: float = 1.0
    terminal_deadline_seconds: float = 900.0
    max_workers: int | None = None

    def __post_init__(self) -> None:
        for value in (self.poll_seconds, self.terminal_deadline_seconds):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ConfigurationError("CONTROLLER_POLICY")
        if self.max_workers is not None and (
            isinstance(self.max_workers, bool)
            or not isinstance(self.max_workers, int)
            or self.max_workers <= 0
        ):
            raise ConfigurationError("CONTROLLER_POLICY")


@dataclass(frozen=True)
class ControllerConfig:
    socket_path: Path
    supervisor_root: Path
    output_dir: Path
    owner: str
    namespace: str
    routes: tuple[AgentRoute, ...]
    request_limits: RequestLimits = RequestLimits()
    controller_policy: ControllerPolicy = ControllerPolicy()

    def __post_init__(self) -> None:
        for field_name in ("socket_path", "supervisor_root", "output_dir"):
            value = getattr(self, field_name)
            if not isinstance(value, Path):
                object.__setattr__(self, field_name, Path(value))
        for value in (self.owner, self.namespace):
            if not isinstance(value, str) or not value or not value.isprintable():
                raise ConfigurationError("CALLER_IDENTITY")
        if not self.routes:
            raise ConfigurationError("AGENT_ROUTE")
        ids = [route.agent_id for route in self.routes]
        if len(ids) != len(set(ids)):
            raise ConfigurationError("AGENT_ROUTE")


@dataclass(frozen=True)
class LivePolicy:
    package_version: str
    api_version: int
    capacity: int
    event_page_limit: int
    max_run_event_budget_bytes: int
    max_prompt_bytes: int

    def workers_for(self, config: ControllerConfig) -> int:
        workers = min(len(config.routes), self.capacity)
        if config.controller_policy.max_workers is not None:
            workers = min(workers, config.controller_policy.max_workers)
        return max(1, workers)


@dataclass(frozen=True)
class RunProof:
    verdict: str
    first_failure: str | None
    result: dict[str, Any]
    raw_receipt: dict[str, Any]


def parse_agent_route(raw: str) -> AgentRoute:
    if not isinstance(raw, str) or not raw.isprintable():
        raise ConfigurationError("AGENT_ROUTE")
    agent_id, separator, remainder = raw.partition("=")
    model, comma, effort = remainder.rpartition(",")
    if not separator or not comma:
        raise ConfigurationError("AGENT_ROUTE")
    return AgentRoute(agent_id=agent_id, model=model, effort=effort)


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--socket", type=Path, required=True, help="deployed arsd Unix socket path")
    parser.add_argument(
        "--supervisor-root",
        type=Path,
        required=True,
        help="ARS supervisor state root containing native-runs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="fresh controller evidence root; existing paths are refused",
    )
    parser.add_argument("--owner", required=True, help="caller-authorized owner literal")
    parser.add_argument("--namespace", required=True, help="caller-authorized namespace literal")
    parser.add_argument(
        "--agent",
        action="append",
        required=True,
        metavar="AGENT_ID=MODEL,EFFORT",
        help="repeatable exact registered route",
    )
    parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=RequestLimits.startup_timeout_seconds,
        help="ARS request-limit policy (default: 120)",
    )
    parser.add_argument(
        "--turn-timeout-seconds",
        type=float,
        default=RequestLimits.turn_timeout_seconds,
        help="ARS request-limit policy (default: 600)",
    )
    parser.add_argument(
        "--cancel-grace-seconds",
        type=float,
        default=RequestLimits.cancel_grace_seconds,
        help="ARS request-limit policy (default: 10)",
    )
    parser.add_argument(
        "--max-stderr-bytes",
        type=int,
        default=RequestLimits.max_stderr_bytes,
        help="ARS request-limit policy (default: 1048576)",
    )
    parser.add_argument(
        "--max-event-bytes",
        type=int,
        default=RequestLimits.max_event_bytes,
        help="ARS request-limit policy (default: 131072)",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=RequestLimits.max_events,
        help="ARS request-limit policy (default: 8000)",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=ControllerPolicy.poll_seconds,
        help="controller polling policy (default: 1)",
    )
    parser.add_argument(
        "--controller-deadline-seconds",
        type=float,
        default=ControllerPolicy.terminal_deadline_seconds,
        help="controller observation deadline, not a Run timeout (default: 900)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="optional controller cap; live server capacity remains authoritative",
    )


def config_from_namespace(args: argparse.Namespace) -> ControllerConfig:
    routes = tuple(parse_agent_route(raw) for raw in args.agent)
    return ControllerConfig(
        socket_path=args.socket,
        supervisor_root=args.supervisor_root,
        output_dir=args.output_dir,
        owner=args.owner,
        namespace=args.namespace,
        routes=routes,
        request_limits=RequestLimits(
            startup_timeout_seconds=args.startup_timeout_seconds,
            turn_timeout_seconds=args.turn_timeout_seconds,
            cancel_grace_seconds=args.cancel_grace_seconds,
            max_stderr_bytes=args.max_stderr_bytes,
            max_event_bytes=args.max_event_bytes,
            max_events=args.max_events,
        ),
        controller_policy=ControllerPolicy(
            poll_seconds=args.poll_seconds,
            terminal_deadline_seconds=args.controller_deadline_seconds,
            max_workers=args.max_workers,
        ),
    )


VERSION_UNREPORTED = "unreported"
_SAFE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")


def diagnostic_version(value: Any) -> str:
    """The served package version, or a sentinel.

    A version is diagnostic metadata and never an admission gate, so an absent,
    empty, non-string, or unsafely shaped value must not stop the batch: it
    becomes one sentinel and the same fixed cases run anyway.
    """

    if isinstance(value, str) and _SAFE_VERSION.fullmatch(value):
        return value
    return VERSION_UNREPORTED


def live_projection(live: LivePolicy) -> dict[str, Any]:
    """The only live-daemon facts a controller persists.

    ``server_info`` is a daemon-authored document that may carry operator,
    host, or deployment detail this skill has no business retaining, so the
    validated policy numbers are projected out and the document is dropped.
    """

    return {
        "api_version": live.api_version,
        "ars_package_version": live.package_version,
        "max_concurrent_runs": live.capacity,
        "events_page_limit": live.event_page_limit,
        "max_run_event_budget_bytes": live.max_run_event_budget_bytes,
        "max_prompt_bytes": live.max_prompt_bytes,
    }


def preflight(config: ControllerConfig, client_factory: Callable[..., Any]) -> tuple[LivePolicy, dict[str, Any]]:
    try:
        with client_factory(config.socket_path) as client:
            info = client.server_info(request_id="quick-health-info-" + secrets.token_hex(8))
    except Exception:
        raise PreflightError("SERVER_INFO") from None
    if type(info) is not dict:
        raise PreflightError("SERVER_INFO_SHAPE")
    api_version = info.get("api_version")
    supported = info.get("supported_api_versions")
    if (
        isinstance(api_version, bool)
        or not isinstance(api_version, int)
        or api_version <= 0
        or type(supported) is not list
        or api_version not in supported
    ):
        raise PreflightError("API_VERSION")
    operations = info.get("operations")
    if type(operations) is not list or not REQUIRED_OPERATIONS <= set(operations):
        raise PreflightError("OPERATIONS")
    limits = info.get("limits")
    if type(limits) is not dict:
        raise PreflightError("LIVE_LIMITS")

    names = (
        "max_concurrent_runs",
        "events_page_limit",
        "max_run_event_budget_bytes",
        "max_prompt_bytes",
    )
    values: dict[str, int] = {}
    for name in names:
        value = limits.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise PreflightError("LIVE_LIMITS")
        values[name] = value
    event_budget = (
        config.request_limits.max_event_bytes * config.request_limits.max_events
    )
    if event_budget > values["max_run_event_budget_bytes"]:
        raise PreflightError("EVENT_BUDGET")
    live = LivePolicy(
        package_version=diagnostic_version(info.get("version")),
        api_version=api_version,
        capacity=values["max_concurrent_runs"],
        event_page_limit=values["events_page_limit"],
        max_run_event_budget_bytes=values["max_run_event_budget_bytes"],
        max_prompt_bytes=values["max_prompt_bytes"],
    )
    # The daemon document itself stops here: only the projection travels on.
    return live, live_projection(live)


def create_output_root(config: ControllerConfig, diagnostics: Mapping[str, Any]) -> Path:
    root = config.output_dir.resolve(strict=False)
    try:
        root.mkdir(mode=0o700, exist_ok=False)
        (root / "raw").mkdir(mode=0o700)
        (root / "workspaces").mkdir(mode=0o700)
        write_json_exclusive(root / "raw" / "live-policy.json", diagnostics)
    except FileExistsError:
        raise EvidenceError("OUTPUT_EXISTS") from None
    except OSError:
        raise EvidenceError("OUTPUT_CREATE") from None
    return root


def child_path(root: Path, *parts: str) -> Path:
    candidate = root.joinpath(*parts).resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise EvidenceError("PATH_ESCAPE")
    return candidate


def write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(dict(payload), stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
    except FileExistsError:
        raise EvidenceError("OUTPUT_EXISTS") from None
    except OSError:
        raise EvidenceError("OUTPUT_WRITE") from None


def sha256_ref(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_payload(
    config: ControllerConfig,
    route: AgentRoute,
    *,
    case_ref: str,
    prompt: str,
    workspace: Path,
    session_id: str | None = None,
    capabilities: Sequence[str] = ("read",),
    grant_label: str = "read-only",
) -> dict[str, Any]:
    """One immutable per-Run request. The grant is the case's own capability
    set: a controller that always sent the same grant could never distinguish a
    denied family from an ungranted one."""

    grant_name = f"ars-quick-health-{grant_label}"
    request: dict[str, Any] = {
        "owner": config.owner,
        "namespace": config.namespace,
        "agent_id": route.agent_id,
        "expected_binding_hash": None,
        "input_refs": [{"ref": case_ref, "content_hash": sha256_ref(prompt)}],
        "requested_model": route.model,
        "requested_effort": route.effort,
        "grant_ref": f"grant:{grant_name}",
        "grant_hash": sha256_ref(grant_name),
        "grant_role_hash": sha256_ref("ars-quick-health-controller"),
        "grant_capabilities": list(capabilities),
        "mcp_snapshot_hashes": [],
        "credential_refs": [],
        "limits": config.request_limits.to_wire(),
        "evidence_policy_hash": sha256_ref("ars-quick-health-evidence"),
        "recovery_policy_hash": sha256_ref("ars-quick-health-no-retry"),
    }
    if session_id is not None:
        request["session_id"] = session_id
    return {
        "request": request,
        "prompt_text": prompt,
        "workspace_root": str(workspace),
        "cwd": str(workspace),
        "retry_of_run_id": None,
    }


def await_terminal(
    client: Any,
    run_id: str,
    policy: ControllerPolicy,
    *,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    deadline = time.monotonic() + policy.terminal_deadline_seconds
    while time.monotonic() < deadline:
        status = client.run_status(run_id)
        result = status.get("result") if isinstance(status, Mapping) else None
        if isinstance(result, dict) and result.get("status") in TERMINAL_STATUSES:
            return result
        sleeper(policy.poll_seconds)
    raise CaseFailure("TERMINAL_TIMEOUT")


def read_all_events(
    client: Any,
    run_id: str,
    *,
    page_limit: int,
    max_events: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cursor = 0
    max_pages = math.ceil(max_events / page_limit) + 1
    for _ in range(max_pages):
        page = client.run_events(run_id, from_seq=cursor, limit=page_limit)
        if not isinstance(page, Mapping) or type(page.get("events")) is not list:
            raise CaseFailure("EVENT_EVIDENCE")
        rows = page["events"]
        if any(type(row) is not dict for row in rows):
            raise CaseFailure("EVENT_EVIDENCE")
        events.extend(rows)
        if len(events) > max_events:
            raise CaseFailure("EVENT_EVIDENCE")
        if page.get("exhausted") is True:
            return events
        next_cursor = page.get("next_from_seq")
        if (
            isinstance(next_cursor, bool)
            or not isinstance(next_cursor, int)
            or next_cursor <= cursor
        ):
            raise CaseFailure("EVENT_EVIDENCE")
        cursor = next_cursor
    raise CaseFailure("EVENT_EVIDENCE")


def safe_component(value: Any) -> str:
    """An ack identifier safe to use as one path component, or a stop."""


    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or not value.isprintable()
    ):
        raise CaseFailure("DURABLE_EVIDENCE")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise OSError
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise CaseFailure("DURABLE_EVIDENCE") from None
    if type(payload) is not dict:
        raise CaseFailure("DURABLE_EVIDENCE")
    return payload


def read_run_evidence(
    config: ControllerConfig, run_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """That Run's own durable ``effective.json`` and sealed ``spec.json``."""

    run_dir = config.supervisor_root.resolve(strict=False) / "native-runs" / run_id
    effective = _read_json(run_dir / "effective.json")
    spec = _read_json(run_dir / "spec.json")
    if type(spec.get("runtime")) is not dict:
        raise CaseFailure("DURABLE_EVIDENCE")
    return effective, spec


def config_fidelity_checks(
    route: AgentRoute,
    effective: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> dict[str, bool]:
    """Exact requested/sealed/effective model and effort, or nothing."""

    runtime = spec.get("runtime")
    runtime = runtime if isinstance(runtime, Mapping) else {}
    return {
        "effective_model": effective.get("effective_model") == route.model,
        "effective_effort": effective.get("effective_effort") == route.effort,
        "spec_model": runtime.get("model_id") == route.model,
        "spec_effort": runtime.get("effort") == route.effort,
        "config_fidelity": runtime.get("config_fidelity") == "exact",
    }


def classify_reap(
    effective: Mapping[str, Any],
    liveness_checker: Callable[[Mapping[str, Any]], str] = classify_holder,
) -> str:
    """Liveness of this Run's own recorded process identity; never a scan."""

    identity = effective.get("process_identity")
    if not isinstance(identity, Mapping):
        return "unknown"
    try:
        return liveness_checker(identity)
    except Exception:
        return "unknown"


def prove_run(
    config: ControllerConfig,
    route: AgentRoute,
    *,
    ack: Mapping[str, Any],
    request_id: str,
    result: dict[str, Any],
    events: Sequence[Mapping[str, Any]],
    session_operation: str,
    liveness_checker: Callable[[Mapping[str, Any]], str] = classify_holder,
) -> RunProof:
    run_id = safe_component(ack.get("run_id"))
    session_id = safe_component(ack.get("session_id"))
    effective, spec = read_run_evidence(config, run_id)

    families = [str(event.get("type", "")) for event in events]
    checks: dict[str, bool | None] = {
        "completed": result.get("status") == "completed",
        "end_turn": result.get("stop_reason") == "end_turn",
        **config_fidelity_checks(route, effective, spec),
        "prompt_once": families.count("session_prompt_sent") == 1,
    }
    if session_operation == "create":
        checks["session_new_once"] = families.count("session_new_requested") == 1
        checks["session_load_absent"] = "session_load_requested" not in families
    elif session_operation == "load":
        checks["session_load_once"] = families.count("session_load_requested") == 1
        checks["session_new_absent"] = "session_new_requested" not in families
    else:
        raise ConfigurationError("SESSION_OPERATION")

    liveness = classify_reap(effective, liveness_checker)
    checks["process_reaped"] = liveness == CRASHED

    failure: str | None = None
    verdict = "PASS"
    ordered_failures = (
        ("completed", "TERMINAL"),
        ("end_turn", "STOP_REASON"),
        ("effective_model", "CONFIG_FIDELITY"),
        ("effective_effort", "CONFIG_FIDELITY"),
        ("spec_model", "CONFIG_FIDELITY"),
        ("spec_effort", "CONFIG_FIDELITY"),
        ("config_fidelity", "CONFIG_FIDELITY"),
        ("prompt_once", "PROMPT_EVENTS"),
        ("session_new_once", "SESSION_NEW_EVENTS"),
        ("session_load_absent", "SESSION_NEW_EVENTS"),
        ("session_load_once", "SESSION_LOAD_EVENTS"),
        ("session_new_absent", "SESSION_RECREATED"),
    )
    for check, category in ordered_failures:
        if check in checks and checks[check] is not True:
            failure = category
            verdict = "FAIL"
            break
    if failure is None and checks["process_reaped"] is not True:
        failure = "PROCESS_REAP_UNPROVEN"
        verdict = "FAIL" if liveness == "alive" else "INDETERMINATE"

    raw_receipt = {
        "request_id": request_id,
        "run_id": run_id,
        "session_id": session_id,
        "agent_id": route.agent_id,
        "requested_model": route.model,
        "requested_effort": route.effort,
        "event_family_counts": {
            family: families.count(family) for family in sorted(set(families))
        },
        "checks": checks,
        "verdict": verdict,
        "first_failure": failure,
    }
    return RunProof(
        verdict=verdict,
        first_failure=failure,
        result=result,
        raw_receipt=raw_receipt,
    )


def request_id(label: str) -> str:
    return f"quick-health-{label}-{secrets.token_hex(12)}"


def summarize_overall(results: Sequence[Mapping[str, Any]]) -> str:
    verdicts = [row.get("verdict") for row in results]
    if verdicts and all(verdict == "PASS" for verdict in verdicts):
        return "PASS"
    if any(verdict == "FAIL" for verdict in verdicts):
        return "FAIL"
    return "INDETERMINATE"


def write_summary(root: Path, summary: Mapping[str, Any]) -> None:
    write_json_exclusive(root / "summary.json", summary)

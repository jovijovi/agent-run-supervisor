"""Hermetic contract tests for the two direct ARS quick-health controllers.

The tests use a public-client-shaped fake and synthetic durable Run evidence.
They never start arsd, an external AGENT, or a provider call.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "ars-batch-agent-acceptance"
SCRIPTS = SKILL / "scripts"
COMMON_PATH = SCRIPTS / "_common.py"
RESPONSE_PATH = SCRIPTS / "run_response_only.py"
SESSION_PATH = SCRIPTS / "run_session_reuse.py"


def _load(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"required script is absent: {path.relative_to(ROOT)}"
    script_dir = str(path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _modules() -> tuple[ModuleType, ModuleType, ModuleType]:
    common = _load(COMMON_PATH, "_common")
    response = _load(RESPONSE_PATH, "ars_response_only_test_view")
    session = _load(SESSION_PATH, "ars_session_reuse_test_view")
    return common, response, session


class _FakeClient:
    """Public-ArsdClient-shaped fake with synthetic durable evidence."""

    lock = threading.Lock()
    supervisor_root: Path
    submissions: list[dict]
    results: dict[str, dict]
    events: dict[str, list[dict]]
    tokens: dict[str, str]
    response_message: object
    s1_message: object
    s2_message: object | None
    mutate_workspace: bool
    effective_model: str | None
    omit_prompt_event: bool
    change_session_on_load: bool
    reuse_run_on_load: bool
    info_overrides: dict

    @classmethod
    def configure(cls, supervisor_root: Path) -> None:
        cls.supervisor_root = supervisor_root
        cls.submissions = []
        cls.results = {}
        cls.events = {}
        cls.tokens = {}
        cls.response_message = "plain deliverable; intentionally not JSON or Python"
        cls.s1_message = "acknowledged in arbitrary words"
        cls.s2_message = None
        cls.mutate_workspace = False
        cls.effective_model = None
        cls.omit_prompt_event = False
        cls.change_session_on_load = False
        cls.reuse_run_on_load = False
        cls.info_overrides = {}

    def __init__(self, socket_path: Path | str):
        self.socket_path = Path(socket_path)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def server_info(self, *, request_id: str | None = None) -> dict:
        del request_id
        info = {
            "version": "test-package-version",
            "api_version": 19,
            "supported_api_versions": [19],
            "operations": ["server_info", "submit", "run_status", "run_events"],
            "limits": {
                "max_concurrent_runs": 2,
                "max_prompt_bytes": 200_000,
                "events_page_limit": 50,
                "max_run_event_budget_bytes": 50_000_000,
            },
        }
        for key, value in self.info_overrides.items():
            if key == "limits":
                info["limits"].update(value)
            else:
                info[key] = value
        return info

    def submit(self, *, request_id: str, payload: dict) -> dict:
        request = payload["request"]
        reference = request["input_refs"][0]["ref"]
        is_load = "session-reuse:S2" in reference
        with self.lock:
            ordinal = len(self.submissions) + 1
            run_id = "run-1" if is_load and self.reuse_run_on_load else f"run-{ordinal}"
            session_id = request.get("session_id", f"session-{ordinal}")
            if is_load and self.change_session_on_load:
                session_id = "unexpected-session"
            self.submissions.append(
                {
                    "request_id": request_id,
                    "payload": payload,
                    "run_id": run_id,
                    "session_id": session_id,
                }
            )

        workspace = Path(payload["workspace_root"])
        if self.mutate_workspace:
            (workspace / "agent-write.txt").write_text("changed", encoding="utf-8")

        if ":response-only:" in reference:
            final_message = self.response_message
            event_types = ["session_new_requested", "session_prompt_sent"]
        elif "session-reuse:S1" in reference:
            match = re.search(r"CONTINUITY-[A-F0-9]+", payload["prompt_text"])
            assert match is not None
            self.tokens[session_id] = match.group(0)
            final_message = self.s1_message
            event_types = ["session_new_requested", "session_prompt_sent"]
        else:
            final_message = self.s2_message
            if final_message is None:
                final_message = self.tokens[request["session_id"]]
            event_types = ["session_load_requested", "session_prompt_sent"]
        if self.omit_prompt_event:
            event_types.remove("session_prompt_sent")

        self.results[run_id] = {
            "status": "completed",
            "stop_reason": "end_turn",
            "detail_code": None,
            "retryable": False,
            "final_message": final_message,
        }
        self.events[run_id] = [
            {"seq": index, "type": event_type}
            for index, event_type in enumerate(event_types, start=1)
        ]
        run_dir = self.supervisor_root / "native-runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        effective_model = self.effective_model or request["requested_model"]
        (run_dir / "effective.json").write_text(
            json.dumps(
                {
                    "process_identity": {
                        "pid": 12345,
                        "process_start": "123",
                        "boot_id": "test-boot",
                        "host": "test-host",
                    },
                    "effective_model": effective_model,
                    "effective_effort": request["requested_effort"],
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "spec.json").write_text(
            json.dumps(
                {
                    "runtime": {
                        "model_id": request["requested_model"],
                        "effort": request["requested_effort"],
                        "config_fidelity": "exact",
                    }
                }
            ),
            encoding="utf-8",
        )
        return {
            "run_id": run_id,
            "session_id": session_id,
            "accepted_at": "test-time",
        }

    def run_status(self, run_id: str) -> dict:
        return {"run_id": run_id, "result": self.results[run_id]}

    def run_events(self, run_id: str, *, from_seq: int, limit: int, **kwargs) -> dict:
        del kwargs
        selected = [event for event in self.events[run_id] if event["seq"] > from_seq]
        page = selected[:limit]
        return {
            "run_id": run_id,
            "events": page,
            "next_from_seq": page[-1]["seq"] if page else from_seq,
            "exhausted": len(page) == len(selected),
        }


def _config(tmp_path: Path, common: ModuleType, output_name: str):
    route = common.AgentRoute("agent-a", "provider/exact-model", "exact-effort")
    return common.ControllerConfig(
        socket_path=tmp_path / "arsd.sock",
        supervisor_root=_FakeClient.supervisor_root,
        output_dir=tmp_path / output_name,
        owner="private-owner",
        namespace="private/namespace",
        routes=(route,),
        request_limits=common.RequestLimits(
            startup_timeout_seconds=5,
            turn_timeout_seconds=10,
            cancel_grace_seconds=1,
            max_stderr_bytes=4096,
            max_event_bytes=1024,
            max_events=100,
        ),
        controller_policy=common.ControllerPolicy(
            poll_seconds=0.001,
            terminal_deadline_seconds=1,
        ),
    )


@pytest.fixture
def controller_setup(tmp_path: Path):
    common, response, session = _modules()
    supervisor_root = tmp_path / "supervisor-state"
    supervisor_root.mkdir()
    _FakeClient.configure(supervisor_root)
    return common, response, session


def _reaped(identity: dict) -> str:
    assert identity["pid"] == 12345
    return "crashed"


def test_skill_keeps_only_official_parameterized_controllers() -> None:
    present = {
        str(path.relative_to(SKILL)) for path in SKILL.rglob("*") if path.is_file()
    }
    assert {
        "SKILL.md",
        "scripts/_common.py",
        "scripts/run_response_only.py",
        "scripts/run_session_reuse.py",
        "scripts/run_permissions.py",
        "references/test-matrix.md",
        "references/evidence-contract.md",
        "references/response-only-controller.md",
        "references/permissions-controller.md",
    } <= present
    assert "scripts/run_batch_acceptance.py" not in present
    assert "scripts/adjudicate.py" not in present
    assert list((ROOT / "tests").glob("test_ars_batch_agent_acceptance*")) == [
        ROOT / "tests/test_ars_batch_agent_acceptance_skill.py"
    ]
    assert RESPONSE_PATH.stat().st_mode & 0o111
    assert SESSION_PATH.stat().st_mode & 0o111
    assert PERMISSIONS_PATH.stat().st_mode & 0o111


def test_response_controller_is_a_compact_delivery_harness() -> None:
    source = RESPONSE_PATH.read_text(encoding="utf-8")
    assert len(source.splitlines()) < 400


def test_docs_define_delivery_health_and_content_as_out_of_scope() -> None:
    documents = [
        SKILL / "SKILL.md",
        SKILL / "references/evidence-contract.md",
        SKILL / "references/response-only-controller.md",
        SKILL / "references/test-matrix.md",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in documents).lower()
    assert "content correctness" in text
    assert "quality" in text
    assert "out of scope" in text


def test_route_parser_and_both_clis_preserve_exact_parameters(
    controller_setup, tmp_path: Path
) -> None:
    common, response, session = controller_setup
    route = common.parse_agent_route("agent-b=opaque[effort=high,fast=true],N/A")
    assert (route.agent_id, route.model, route.effort) == (
        "agent-b",
        "opaque[effort=high,fast=true]",
        "N/A",
    )
    argv = [
        "--socket",
        str(tmp_path / "arsd.sock"),
        "--supervisor-root",
        str(tmp_path / "state"),
        "--output-dir",
        str(tmp_path / "evidence"),
        "--owner",
        "caller-owner",
        "--namespace",
        "caller/namespace",
        "--agent",
        "agent-a=provider/model,high",
    ]
    for module in (response, session):
        config = module.parse_args(argv).config
        assert config.socket_path == tmp_path / "arsd.sock"
        assert config.routes[0].model == "provider/model"
        assert config.routes[0].effort == "high"


def test_response_only_accepts_any_nonempty_string_as_the_deliverable(
    controller_setup, tmp_path: Path
) -> None:
    common, response, _ = controller_setup
    config = _config(tmp_path, common, "response-evidence")

    summary = response.run_response_only(
        config, client_factory=_FakeClient, sleeper=lambda _: None, liveness_checker=_reaped
    )

    assert summary["overall"] == "PASS"
    assert len(_FakeClient.submissions) == 3
    assert all("session_id" not in row["payload"]["request"] for row in _FakeClient.submissions)
    assert all(
        not any(path.iterdir())
        for path in (config.output_dir / "workspaces").glob("*/*")
    )
    expected_checks = {
        "completed",
        "end_turn",
        "effective_model",
        "effective_effort",
        "spec_model",
        "spec_effort",
        "config_fidelity",
        "prompt_once",
        "session_new_once",
        "session_load_absent",
        "process_reaped",
        "workspace_unchanged",
        "deliverable_present",
    }
    for receipt_path in (config.output_dir / "raw").glob("R*.json"):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert set(receipt["checks"]) == expected_checks
        assert all(receipt["checks"].values())
        assert "final_message" not in receipt


@pytest.mark.parametrize("message", ["", None])
def test_response_only_refuses_a_missing_deliverable(
    controller_setup, tmp_path: Path, message: object
) -> None:
    common, response, _ = controller_setup
    _FakeClient.response_message = message
    config = _config(tmp_path, common, "missing-deliverable")

    summary = response.run_response_only(
        config, client_factory=_FakeClient, sleeper=lambda _: None, liveness_checker=_reaped
    )

    assert summary["overall"] == "FAIL"
    assert summary["results"][0]["first_failure"] == "DELIVERABLE_MISSING"


@pytest.mark.parametrize(
    ("mutation", "liveness", "expected_verdict", "expected_failure"),
    [
        ("workspace", _reaped, "FAIL", "WORKSPACE_MUTATED"),
        ("model", _reaped, "FAIL", "CONFIG_FIDELITY"),
        ("prompt", _reaped, "FAIL", "PROMPT_EVENTS"),
        (None, lambda _: "unknown", "INDETERMINATE", "PROCESS_REAP_UNPROVEN"),
    ],
)
def test_response_only_fails_closed_on_missing_chain_evidence(
    controller_setup,
    tmp_path: Path,
    mutation: str | None,
    liveness,
    expected_verdict: str,
    expected_failure: str,
) -> None:
    common, response, _ = controller_setup
    _FakeClient.mutate_workspace = mutation == "workspace"
    _FakeClient.effective_model = "wrong-model" if mutation == "model" else None
    _FakeClient.omit_prompt_event = mutation == "prompt"
    config = _config(tmp_path, common, "failed-response-proof")

    summary = response.run_response_only(
        config, client_factory=_FakeClient, sleeper=lambda _: None, liveness_checker=liveness
    )

    assert summary["overall"] == expected_verdict
    assert summary["results"][0]["first_failure"] == expected_failure


def test_session_reuse_proves_create_load_and_exact_token_continuity(
    controller_setup, tmp_path: Path
) -> None:
    common, _, session = controller_setup
    config = _config(tmp_path, common, "session-evidence")
    token = "CONTINUITY-" + "A" * 32

    summary = session.run_session_reuse(
        config,
        client_factory=_FakeClient,
        sleeper=lambda _: None,
        liveness_checker=_reaped,
        token_factory=lambda: token,
    )

    assert summary["overall"] == "PASS"
    assert len(_FakeClient.submissions) == 2
    first, second = _FakeClient.submissions
    assert first["run_id"] != second["run_id"]
    assert second["payload"]["request"]["session_id"] == first["session_id"]
    receipt = json.loads(
        (config.output_dir / "raw/session-reuse-agent-a.json").read_text(encoding="utf-8")
    )
    assert receipt["S1"]["checks"]["deliverable_present"] is True
    assert all(receipt["S1"]["checks"].values())
    assert all(receipt["S2"]["checks"].values())
    assert receipt["S1"]["event_family_counts"] == {
        "session_new_requested": 1,
        "session_prompt_sent": 1,
    }
    assert receipt["S2"]["event_family_counts"] == {
        "session_load_requested": 1,
        "session_prompt_sent": 1,
    }
    assert receipt["continuity"] == {
        "distinct_runs": True,
        "same_session": True,
        "token_exact_match": True,
    }
    encoded = json.dumps(receipt)
    assert token not in encoded
    assert _FakeClient.s1_message not in encoded


def test_session_reuse_requires_nonempty_s1_output_before_s2(
    controller_setup, tmp_path: Path
) -> None:
    common, _, session = controller_setup
    _FakeClient.s1_message = ""
    config = _config(tmp_path, common, "empty-s1")

    summary = session.run_session_reuse(
        config,
        client_factory=_FakeClient,
        sleeper=lambda _: None,
        liveness_checker=_reaped,
        token_factory=lambda: "CONTINUITY-" + "B" * 32,
    )

    assert summary["overall"] == "FAIL"
    assert summary["results"][0]["first_failure"] == "S1_DELIVERABLE_MISSING"
    assert len(_FakeClient.submissions) == 1


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        ("token", "TOKEN_MISMATCH"),
        ("session", "SESSION_CHANGED"),
        ("run", "RUNS_NOT_DISTINCT"),
    ],
)
def test_session_reuse_rejects_broken_continuity(
    controller_setup, tmp_path: Path, mutation: str, failure: str
) -> None:
    common, _, session = controller_setup
    _FakeClient.s2_message = "wrong-token" if mutation == "token" else None
    _FakeClient.change_session_on_load = mutation == "session"
    _FakeClient.reuse_run_on_load = mutation == "run"
    config = _config(tmp_path, common, "broken-continuity")

    summary = session.run_session_reuse(
        config,
        client_factory=_FakeClient,
        sleeper=lambda _: None,
        liveness_checker=_reaped,
        token_factory=lambda: "CONTINUITY-" + "C" * 32,
    )

    assert summary["overall"] == "FAIL"
    assert summary["results"][0]["first_failure"] == failure


def test_shareable_summaries_exclude_local_and_private_values(
    controller_setup, tmp_path: Path
) -> None:
    common, response, _ = controller_setup
    config = _config(tmp_path, common, "sanitized-summary")

    summary = response.run_response_only(
        config, client_factory=_FakeClient, sleeper=lambda _: None, liveness_checker=_reaped
    )

    persisted = json.loads((config.output_dir / "summary.json").read_text(encoding="utf-8"))
    assert persisted == summary
    encoded = json.dumps(summary)
    for private in (
        str(tmp_path),
        config.owner,
        config.namespace,
        "run-1",
        "session-1",
        str(_FakeClient.response_message),
    ):
        assert private not in encoded


def test_committed_skill_material_has_no_checkout_specific_path() -> None:
    material = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SKILL.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    assert str(ROOT) not in material




# ---------------------------------------------------------------------------
# Permission-mediation controller
# ---------------------------------------------------------------------------

PERMISSIONS_PATH = SCRIPTS / "run_permissions.py"
_TOKEN = re.compile(r"ARSPERM-[A-F0-9]{32}")
#: A filename the AGENT chose, not the controller.
_AGENT_NAME = "<agent-chosen>.txt"

#: What a healthy chain does for each fixed Case — mediated operation, tool
#: kind, decision. Written out here independently of the controller's own case
#: table, so a silent drift in either one breaks these tests.
_CHAIN = {
    "P1-READ-ALLOW": ("fs_read", "read", "allow"),
    "P2-WRITE-DENY": ("permission:edit", "edit", "deny"),
    "P3-SEARCH-ALLOW": ("permission:search", "search", "allow"),
    "P4-EXECUTE-DENY": ("permission:execute", "execute", "deny"),
    "P5-EXECUTE-ALLOW": ("permission:execute", "execute", "allow"),
    "P6-OUTSIDE-READ-DENY": ("fs_read", "read", "deny"),
    "P7-SYMLINK-READ-DENY": ("fs_read", "read", "deny"),
    "P8-EDIT-EXISTING-DENY": ("permission:edit", "edit", "deny"),
}


class _FakeChain:
    """ARS-shaped fake: one Run per submission, scripted per Case."""

    lock = threading.Lock()
    supervisor_root: Path

    @classmethod
    def configure(cls, supervisor_root: Path) -> None:
        cls.supervisor_root = supervisor_root
        cls.submissions = []
        cls.cancels = []
        cls.results = {}
        cls.events = {}
        cls.status_calls = {}
        cls.stalled = frozenset()
        cls.stall_polls = 10**6
        cls.omit_mediation = frozenset()
        cls.omit_tool_events = frozenset()
        cls.flip_decision = frozenset()
        cls.violation = frozenset()
        cls.violation_kind = {}
        cls.side_effect = frozenset()
        cls.leak_token = frozenset()
        cls.no_effect = frozenset()
        cls.hostile_filename = frozenset()
        cls.detail_code = {}
        cls.terminal = {}
        cls.effective_model = None
        cls.info_overrides = {}

    def __init__(self, socket_path: Path | str):
        self.socket_path = Path(socket_path)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def server_info(self, *, request_id: str | None = None) -> dict:
        del request_id
        info = {
            "version": "test-package-version",
            "api_version": 19,
            "supported_api_versions": [19],
            "operations": ["server_info", "submit", "run_status", "run_events"],
            "limits": {
                "max_concurrent_runs": 2,
                "max_prompt_bytes": 200_000,
                "events_page_limit": 50,
                "max_run_event_budget_bytes": 50_000_000,
            },
        }
        for key, value in self.info_overrides.items():
            if key == "limits":
                info["limits"].update(value)
            else:
                info[key] = value
        return info

    def submit(self, *, request_id: str, payload: dict) -> dict:
        request = payload["request"]
        case_id = request["input_refs"][0]["ref"].rsplit(":", 1)[-1]
        workspace = Path(payload["workspace_root"])
        op, kind, decision = _CHAIN[case_id]
        if case_id in self.flip_decision:
            decision = "allow" if decision == "deny" else "deny"
        with self.lock:
            ordinal = len(self.submissions) + 1
            run_id = f"run-{ordinal}"
            self.submissions.append(
                {"request_id": request_id, "payload": payload, "case_id": case_id,
                 "run_id": run_id}
            )

        events = [{"type": "session_new_requested"}, {"type": "session_prompt_sent"}]
        if case_id not in self.omit_tool_events:
            events.append({"type": "tool_started", "tool_call_id": f"t-{ordinal}",
                           "kind": kind})
        if case_id not in self.omit_mediation:
            events.append({"type": "permission_mediation", "requested_op": op,
                           "decision": decision, "reason": "scripted"})
        if case_id in self.violation:
            events.append({"type": "permission_violation",
                           "kind": self.violation_kind.get(case_id, kind)})

        status, stop_reason = self.terminal.get(case_id, ("completed", "end_turn"))
        self.results[run_id] = {
            "status": status,
            "stop_reason": stop_reason,
            "detail_code": self.detail_code.get(case_id),
            "final_message": self._act(case_id, workspace, payload["prompt_text"], decision),
        }
        self.events[run_id] = [
            dict(event, seq=index) for index, event in enumerate(events, start=1)
        ]
        if case_id in self.stalled:
            self.status_calls[run_id] = 0

        run_dir = self.supervisor_root / "native-runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "effective.json").write_text(
            json.dumps({
                "process_identity": {"pid": 12345, "process_start": "123",
                                     "boot_id": "test-boot", "host": "test-host"},
                "effective_model": self.effective_model or request["requested_model"],
                "effective_effort": request["requested_effort"],
            }),
            encoding="utf-8",
        )
        (run_dir / "spec.json").write_text(
            json.dumps({"runtime": {"model_id": request["requested_model"],
                                    "effort": request["requested_effort"],
                                    "config_fidelity": "exact"}}),
            encoding="utf-8",
        )
        return {"run_id": run_id, "session_id": f"session-{ordinal}",
                "accepted_at": "test-time"}

    def _act(self, case_id: str, workspace: Path, prompt: str, decision: str) -> str:
        """What the AGENT did in the workspace, and what it replied."""

        token = _TOKEN.search(prompt)
        if case_id in self.no_effect:
            return "I did nothing."
        if case_id in self.hostile_filename:
            (workspace / _AGENT_NAME).write_text("agent chose this", encoding="utf-8")
        if case_id in self.side_effect:
            (workspace / "unasked-for.txt").write_text("side effect", encoding="utf-8")
            return "I did it anyway."
        if case_id in self.leak_token:
            return (workspace / "escape-link.txt").read_text(encoding="utf-8").strip()
        if decision != "allow":
            return "I could not do that."
        if case_id == "P1-READ-ALLOW":
            return (workspace / "probe.txt").read_text(encoding="utf-8").strip()
        if case_id == "P3-SEARCH-ALLOW":
            assert token is not None
            for path in sorted(workspace.iterdir()):
                if token.group(0) in path.read_text(encoding="utf-8"):
                    return path.name
            return "no match"
        if case_id == "P5-EXECUTE-ALLOW":
            assert token is not None
            (workspace / "exec-output.txt").write_text(token.group(0), encoding="utf-8")
        return "done"

    def run_status(self, run_id: str) -> dict:
        if run_id in self.status_calls:
            with self.lock:
                self.status_calls[run_id] += 1
            if self.status_calls[run_id] <= self.stall_polls:
                return {"run_id": run_id, "result": {"status": "running"}}
        return {"run_id": run_id, "result": self.results[run_id]}

    def run_events(self, run_id: str, *, from_seq: int, limit: int, **kwargs) -> dict:
        del kwargs
        selected = [event for event in self.events[run_id] if event["seq"] > from_seq]
        page = selected[:limit]
        return {"run_id": run_id, "events": page,
                "next_from_seq": page[-1]["seq"] if page else from_seq,
                "exhausted": len(page) == len(selected)}

    def run_cancel(self, run_id: str, **kwargs) -> dict:
        self.cancels.append(run_id)
        return {"run_id": run_id}


@pytest.fixture
def permissions(tmp_path: Path):
    common = _load(COMMON_PATH, "_common")
    module = _load(PERMISSIONS_PATH, "ars_permissions_test_view")
    supervisor_root = tmp_path / "permissions-state"
    supervisor_root.mkdir()
    _FakeChain.configure(supervisor_root)
    return common, module


def _permissions_config(
    tmp_path: Path,
    common: ModuleType,
    name: str,
    *,
    agents: tuple[str, ...] = ("agent-a",),
    deadline: float = 1.0,
):
    return common.ControllerConfig(
        socket_path=tmp_path / "arsd.sock",
        supervisor_root=_FakeChain.supervisor_root,
        output_dir=tmp_path / name,
        owner="private-owner",
        namespace="private/namespace",
        routes=tuple(
            common.AgentRoute(agent, "provider/exact-model", "exact-effort")
            for agent in agents
        ),
        request_limits=common.RequestLimits(
            startup_timeout_seconds=5, turn_timeout_seconds=10, cancel_grace_seconds=1,
            max_stderr_bytes=4096, max_event_bytes=1024, max_events=100,
        ),
        controller_policy=common.ControllerPolicy(
            poll_seconds=0.001, terminal_deadline_seconds=deadline
        ),
    )


def _run(module: ModuleType, config, mode: str = "quick", **kwargs):
    return module.run_permissions(
        config,
        mode,
        client_factory=_FakeChain,
        sleeper=kwargs.pop("sleeper", lambda _: None),
        liveness_checker=kwargs.pop("liveness_checker", _reaped),
        **kwargs,
    )


def _case(summary: dict, case_id: str, agent_id: str = "agent-a") -> dict:
    for agent in summary["agents"]:
        if agent["agent_id"] == agent_id:
            for row in agent["cases"]:
                if row["case_id"] == case_id:
                    return row
    raise AssertionError(f"case {case_id} absent for {agent_id}")


def _receipt(config, case_id: str, agent_id: str = "agent-a") -> dict:
    path = config.output_dir / "raw" / f"{case_id}-{agent_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_permissions_controller_is_an_official_entry_point() -> None:
    assert PERMISSIONS_PATH.is_file()
    assert PERMISSIONS_PATH.stat().st_mode & 0o111
    assert (SKILL / "references/permissions-controller.md").is_file()
    # A quick-health script, not a framework.
    assert len(PERMISSIONS_PATH.read_text(encoding="utf-8").splitlines()) < 700


def test_permissions_fixed_modes_hold_the_eight_cases(permissions) -> None:
    _, module = permissions
    quick = [case.case_id for case in module.cases_for_mode("quick")]
    regression = [case.case_id for case in module.cases_for_mode("regression")]
    assert quick == ["P1-READ-ALLOW", "P2-WRITE-DENY"]
    assert regression == [
        "P1-READ-ALLOW", "P2-WRITE-DENY", "P3-SEARCH-ALLOW", "P4-EXECUTE-DENY",
        "P5-EXECUTE-ALLOW", "P6-OUTSIDE-READ-DENY", "P7-SYMLINK-READ-DENY",
        "P8-EDIT-EXISTING-DENY",
    ]
    grants = {case.case_id: case.capabilities for case in module.CASES}
    assert grants["P1-READ-ALLOW"] == ("read",)
    assert grants["P3-SEARCH-ALLOW"] == ("read", "search")
    assert grants["P5-EXECUTE-ALLOW"] == ("read", "execute")
    # No Case grants write, and none expects a write to be allowed.
    assert not any("write" in case.capabilities for case in module.CASES)
    assert not any(
        case.decision == "allow" and "edit" in case.kinds for case in module.CASES
    )


def test_permissions_cli_defaults_to_quick(permissions, tmp_path: Path) -> None:
    _, module = permissions
    argv = [
        "--socket", str(tmp_path / "arsd.sock"),
        "--supervisor-root", str(tmp_path / "state"),
        "--output-dir", str(tmp_path / "evidence"),
        "--owner", "caller-owner",
        "--namespace", "caller/namespace",
        "--agent", "agent-a=provider/model,high",
    ]
    parsed = module.parse_args(argv)
    assert parsed.mode == "quick"
    assert parsed.config.routes[0].model == "provider/model"
    assert module.parse_args(argv + ["--mode", "regression"]).mode == "regression"
    with pytest.raises(SystemExit):
        module.parse_args(argv + ["--mode", "everything"])


def test_permissions_quick_passes_both_fixed_cases(permissions, tmp_path: Path) -> None:
    common, module = permissions
    config = _permissions_config(tmp_path, common, "quick")

    summary = _run(module, config)

    assert summary["overall"] == "PASS"
    assert summary["mode"] == "quick"
    assert [row["case_id"] for row in summary["agents"][0]["cases"]] == [
        "P1-READ-ALLOW", "P2-WRITE-DENY"
    ]
    # One submission per Case, each in its own fresh Session.
    assert len(_FakeChain.submissions) == 2
    assert all(
        "session_id" not in row["payload"]["request"] for row in _FakeChain.submissions
    )
    assert _FakeChain.cancels == []
    receipt = _receipt(config, "P1-READ-ALLOW")
    assert receipt["submission_attempts"] == 1
    assert receipt["grant_capabilities"] == ["read"]
    assert receipt["observed"] == {
        "decision": "allow", "opposite_decision": False, "tool_attempt": True,
        "violation": False, "violation_execute_only": False,
        "status": "completed", "end_turn": True, "effect": True,
    }
    assert all(receipt["checks"].values())
    assert receipt["workspace"]["preserved"] is True


def test_permissions_regression_passes_every_fixed_case(
    permissions, tmp_path: Path
) -> None:
    common, module = permissions
    config = _permissions_config(tmp_path, common, "regression")

    summary = _run(module, config, "regression")

    assert summary["overall"] == "PASS"
    assert len(_FakeChain.submissions) == 8
    grants = {
        row["case_id"]: row["payload"]["request"]["grant_capabilities"]
        for row in _FakeChain.submissions
    }
    assert grants["P4-EXECUTE-DENY"] == ["read"]
    assert grants["P5-EXECUTE-ALLOW"] == ["read", "execute"]
    # The execute-allow Case proves its effect from the workspace itself.
    after = _receipt(config, "P5-EXECUTE-ALLOW")["workspace"]["after"]
    assert len(after) == 1 and next(iter(after.values()))["type"] == "file"


@pytest.mark.parametrize("case_id", ["P1-READ-ALLOW", "P2-WRITE-DENY"])
def test_permissions_reports_unsupported_when_nothing_mediates(
    permissions, tmp_path: Path, case_id: str
) -> None:
    """A read that succeeded, or a write that was stopped, with no mediation."""

    common, module = permissions
    _FakeChain.omit_mediation = frozenset({case_id})
    config = _permissions_config(tmp_path, common, f"unmediated-{case_id.lower()}")

    summary = _run(module, config)

    assert _case(summary, case_id) == {
        "case_id": case_id, "verdict": "UNSUPPORTED",
        "first_failure": "MEDIATION_ABSENT", "warning": None,
    }
    assert summary["overall"] == "UNSUPPORTED"


def test_permissions_is_indeterminate_without_a_tool_attempt(
    permissions, tmp_path: Path
) -> None:
    common, module = permissions
    _FakeChain.omit_tool_events = frozenset({"P1-READ-ALLOW"})
    config = _permissions_config(tmp_path, common, "no-attempt")

    summary = _run(module, config)

    row = _case(summary, "P1-READ-ALLOW")
    assert row["verdict"] == "INDETERMINATE"
    assert row["first_failure"] == "TOOL_ATTEMPT_UNPROVEN"


@pytest.mark.parametrize("route", ["event", "terminal_detail"])
def test_permissions_fails_on_a_permission_violation(
    permissions, tmp_path: Path, route: str
) -> None:
    common, module = permissions
    case = {"P2-WRITE-DENY"}
    _FakeChain.violation = frozenset(case) if route == "event" else frozenset()
    if route == "terminal_detail":
        _FakeChain.detail_code = {"P2-WRITE-DENY": "PERMISSION_VIOLATION"}
        _FakeChain.terminal = {"P2-WRITE-DENY": ("failed", None)}
    config = _permissions_config(tmp_path, common, f"violation-{route}")

    summary = _run(module, config)

    assert summary["overall"] == "FAIL"
    assert _case(summary, "P2-WRITE-DENY")["first_failure"] == "PERMISSION_VIOLATION"


@pytest.mark.parametrize(
    ("agent_id", "case_id", "kind", "verdict"),
    [
        ("codex", "P1-READ-ALLOW", "execute", "WARNING"),
        ("codex", "P1-READ-ALLOW", "read", "FAIL"),
        ("agent-a", "P1-READ-ALLOW", "execute", "FAIL"),
        ("codex", "P2-WRITE-DENY", "execute", "FAIL"),
    ],
)
def test_permissions_codex_p1_execute_violation_is_warning_only(
    permissions, tmp_path: Path, agent_id: str, case_id: str, kind: str, verdict: str
) -> None:
    common, module = permissions
    _FakeChain.violation = frozenset({case_id})
    _FakeChain.violation_kind = {case_id: kind}
    config = _permissions_config(
        tmp_path, common, f"warning-{agent_id}-{case_id}-{kind}", agents=(agent_id,)
    )

    summary = _run(module, config)
    row = _case(summary, case_id, agent_id)

    assert row["verdict"] == verdict
    assert row["warning"] == (
        "CODEX_P1_EXECUTE_VIOLATION" if verdict == "WARNING" else None
    )
    assert _receipt(config, case_id, agent_id)["observed"]["violation"] is True


def test_permissions_warning_aggregates_in_summary(
    permissions, tmp_path: Path
) -> None:
    common, module = permissions
    assert module.aggregate(("PASS", "WARNING")) == "WARNING"
    _FakeChain.violation = frozenset({"P1-READ-ALLOW"})
    _FakeChain.violation_kind = {"P1-READ-ALLOW": "execute"}
    config = _permissions_config(tmp_path, common, "warning-exit", agents=("codex",))
    summary = _run(module, config)
    assert summary["schema_version"] == 2
    assert summary["overall"] == "WARNING"
    assert summary["agents"][0]["warning"] == "CODEX_P1_EXECUTE_VIOLATION"


@pytest.mark.parametrize(
    ("case_id", "hook"), [("P2-WRITE-DENY", "side_effect"), ("P7-SYMLINK-READ-DENY", "leak_token")]
)
def test_permissions_fails_when_a_refusal_did_not_hold(
    permissions, tmp_path: Path, case_id: str, hook: str
) -> None:
    """The file appeared anyway, or the out-of-workspace token came back."""

    common, module = permissions
    setattr(_FakeChain, hook, frozenset({case_id}))
    config = _permissions_config(tmp_path, common, f"ineffective-{hook}")

    summary = _run(module, config, "regression")

    assert _case(summary, case_id)["first_failure"] == "REFUSAL_INEFFECTIVE"
    assert summary["overall"] == "FAIL"
    # The workspace is kept exactly as found.
    assert _receipt(config, case_id)["workspace"]["preserved"] is True


@pytest.mark.parametrize(
    ("case_id", "failure"),
    [("P1-READ-ALLOW", "UNEXPECTED_DENY"), ("P2-WRITE-DENY", "UNEXPECTED_ALLOW")],
)
def test_permissions_fails_when_the_decision_flips(
    permissions, tmp_path: Path, case_id: str, failure: str
) -> None:
    common, module = permissions
    _FakeChain.flip_decision = frozenset({case_id})
    config = _permissions_config(tmp_path, common, f"flip-{case_id.lower()}")

    summary = _run(module, config)

    assert summary["overall"] == "FAIL"
    assert _case(summary, case_id)["first_failure"] == failure


def test_permissions_conditional_execute_allow_is_unsupported(
    permissions, tmp_path: Path
) -> None:
    """A chain that offers no once-scoped execute allow is not a failure."""

    common, module = permissions
    _FakeChain.flip_decision = frozenset({"P5-EXECUTE-ALLOW"})
    config = _permissions_config(tmp_path, common, "conditional")

    summary = _run(module, config, "regression")

    row = _case(summary, "P5-EXECUTE-ALLOW")
    assert row["verdict"] == "UNSUPPORTED"
    assert row["first_failure"] == "CONDITIONAL_ALLOW_UNAVAILABLE"


@pytest.mark.parametrize(
    ("mutation", "liveness", "verdict", "failure"),
    [
        ("model", _reaped, "FAIL", "CONFIG_FIDELITY"),
        ("terminal", _reaped, "INDETERMINATE", "TERMINAL_UNTRUSTWORTHY"),
        ("effect", _reaped, "INDETERMINATE", "EFFECT_UNPROVEN"),
        (None, lambda _: "unknown", "INDETERMINATE", "PROCESS_REAP_UNPROVEN"),
    ],
)
def test_permissions_requires_config_terminal_and_reap_evidence(
    permissions, tmp_path: Path, mutation, liveness, verdict: str, failure: str
) -> None:
    common, module = permissions
    _FakeChain.effective_model = "other-model" if mutation == "model" else None
    if mutation == "terminal":
        _FakeChain.terminal = {"P1-READ-ALLOW": ("unknown", None)}
    _FakeChain.no_effect = frozenset({"P1-READ-ALLOW"}) if mutation == "effect" else frozenset()
    config = _permissions_config(tmp_path, common, f"evidence-{mutation}")

    summary = _run(module, config, liveness_checker=liveness)

    row = _case(summary, "P1-READ-ALLOW")
    assert row["verdict"] == verdict
    assert row["first_failure"] == failure


def test_permissions_denied_run_may_end_in_another_trustworthy_terminal(
    permissions, tmp_path: Path
) -> None:
    common, module = permissions
    _FakeChain.terminal = {"P2-WRITE-DENY": ("failed", None)}
    config = _permissions_config(tmp_path, common, "denied-terminal")

    summary = _run(module, config)

    assert summary["overall"] == "PASS"
    assert _receipt(config, "P2-WRITE-DENY")["observed"]["status"] == "failed"


def test_permissions_reconciles_the_same_run_and_never_replays(
    permissions, tmp_path: Path
) -> None:
    """A controller deadline re-reads the Run; it does not cancel or resubmit."""

    common, module = permissions
    _FakeChain.stalled = frozenset({"P1-READ-ALLOW"})
    _FakeChain.stall_polls = 2
    config = _permissions_config(tmp_path, common, "reconciled", deadline=0.05)

    summary = _run(module, config, sleeper=lambda _: time.sleep(0.03))

    assert summary["overall"] == "PASS"
    assert len(_FakeChain.submissions) == 2
    assert _FakeChain.cancels == []

    # A Run that never reaches a terminal is reported, not killed.
    _FakeChain.configure(_FakeChain.supervisor_root)
    _FakeChain.stalled = frozenset({"P1-READ-ALLOW"})
    stalled_config = _permissions_config(tmp_path, common, "deadline", deadline=0.05)

    stalled = _run(module, stalled_config, sleeper=lambda _: time.sleep(0.03))

    assert _case(stalled, "P1-READ-ALLOW")["first_failure"] == "CONTROLLER_DEADLINE"
    assert _FakeChain.cancels == []
    assert len(_FakeChain.submissions) == 2


def test_permissions_evidence_keeps_no_private_or_agent_text(
    permissions, tmp_path: Path
) -> None:
    common, module = permissions
    _FakeChain.hostile_filename = frozenset({"P2-WRITE-DENY"})
    _FakeChain.info_overrides = {"host": "unit-test-host", "caller_uid": 4242}
    config = _permissions_config(tmp_path, common, "privacy", agents=("agent-a", "agent-b"))

    summary = _run(module, config)

    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(config.output_dir.rglob("*.json"))
    )
    for private in (
        str(tmp_path), "ARSPERM-", "probe.txt", _AGENT_NAME, "<agent-chosen>",
        "unit-test-host", "4242", config.owner, config.namespace,
    ):
        assert private not in persisted, private
    encoded = json.dumps(summary)
    for private in (str(tmp_path), "run-1", "session-1", config.owner, "rank", "score"):
        assert private not in encoded
    assert [agent["agent_id"] for agent in summary["agents"]] == ["agent-a", "agent-b"]


def test_permissions_aggregates_worst_first_and_sets_the_exit_code(
    permissions, tmp_path: Path, monkeypatch, capsys
) -> None:
    common, module = permissions
    assert module.aggregate(("PASS", "UNSUPPORTED")) == "UNSUPPORTED"
    assert module.aggregate(("PASS", "UNSUPPORTED", "INDETERMINATE")) == "INDETERMINATE"
    assert module.aggregate(("INDETERMINATE", "FAIL", "PASS")) == "FAIL"

    monkeypatch.setattr(module, "ArsdClient", _FakeChain, raising=False)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module, "classify_holder", _reaped, raising=False)
    argv = [
        "--socket", str(tmp_path / "arsd.sock"),
        "--supervisor-root", str(_FakeChain.supervisor_root),
        "--output-dir", str(tmp_path / "cli-pass"),
        "--owner", "caller-owner",
        "--namespace", "caller/namespace",
        "--agent", "agent-a=provider/exact-model,exact-effort",
        "--max-event-bytes", "1024",
        "--max-events", "100",
    ]
    assert module.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["overall"] == "PASS"

    _FakeChain.configure(_FakeChain.supervisor_root)
    _FakeChain.violation = frozenset({"P2-WRITE-DENY"})
    argv[5] = str(tmp_path / "cli-fail")
    assert module.main(argv) == 2
    assert json.loads(capsys.readouterr().out)["overall"] == "FAIL"


@pytest.mark.parametrize("reported", ["", None, 7])
def test_permissions_never_gates_on_a_version(
    permissions, tmp_path: Path, reported: object
) -> None:
    """An unreadable served version is diagnostic, never a refusal."""

    common, module = permissions
    _FakeChain.info_overrides = {"version": reported}
    config = _permissions_config(tmp_path, common, f"version-{type(reported).__name__}")

    summary = _run(module, config)

    assert summary["overall"] == "PASS"
    assert summary["ars_package_version"] == "unreported"
    source = PERMISSIONS_PATH.read_text(encoding="utf-8")
    for gate in ("min_version", "version_gate", "binary_hash", "adapter_version"):
        assert gate not in source


def test_permissions_documents_the_cases_and_the_limits() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            SKILL / "SKILL.md",
            SKILL / "references/permissions-controller.md",
            SKILL / "references/test-matrix.md",
            SKILL / "references/evidence-contract.md",
        )
    )
    for case_id in ("P1-READ-ALLOW", "P5-EXECUTE-ALLOW", "P7-SYMLINK-READ-DENY"):
        assert case_id in text
    assert "run_permissions.py" in text
    lowered = text.lower()
    assert "cooperative" in lowered
    assert "not an os sandbox" in lowered
    assert "unsupported" in lowered

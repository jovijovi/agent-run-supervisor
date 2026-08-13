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
        "references/test-matrix.md",
        "references/evidence-contract.md",
        "references/response-only-controller.md",
    } <= present
    assert "scripts/run_batch_acceptance.py" not in present
    assert "scripts/adjudicate.py" not in present
    assert list((ROOT / "tests").glob("test_ars_batch_agent_acceptance*")) == [
        ROOT / "tests/test_ars_batch_agent_acceptance_skill.py"
    ]
    assert RESPONSE_PATH.stat().st_mode & 0o111
    assert SESSION_PATH.stat().st_mode & 0o111


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

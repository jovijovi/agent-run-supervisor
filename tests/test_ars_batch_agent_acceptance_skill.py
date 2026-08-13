"""Focused contract tests for the repository-owned batch acceptance skill.

No test starts arsd, an external AGENT, or a provider call.  The controller is
driven through a typed-client stand-in and the adjudicator reads synthetic local
evidence only.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "ars-batch-agent-acceptance"
RUNNER_PATH = SKILL / "scripts" / "run_batch_acceptance.py"
ADJUDICATOR_PATH = SKILL / "scripts" / "adjudicate.py"


def _load(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"required script is absent: {path.relative_to(ROOT)}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _runner() -> ModuleType:
    return _load(RUNNER_PATH, "ars_batch_acceptance_runner_test_view")


def _adjudicator() -> ModuleType:
    return _load(ADJUDICATOR_PATH, "ars_batch_acceptance_adjudicator_test_view")


def _request(*, agent_id: str, model: str, effort: str) -> dict:
    return {
        "owner": "<caller-owner>",
        "namespace": "<caller-namespace>",
        "agent_id": agent_id,
        "expected_binding_hash": None,
        "input_refs": [
            {"ref": "prompt:inline", "content_hash": "sha256:" + "a" * 64}
        ],
        "requested_model": model,
        "requested_effort": effort,
        "grant_ref": "grant:<reference>",
        "grant_hash": "sha256:" + "b" * 64,
        "grant_role_hash": "sha256:" + "c" * 64,
        "grant_capabilities": ["read"],
        "mcp_snapshot_hashes": [],
        "credential_refs": [],
        "limits": {
            "startup_timeout_seconds": 5,
            "turn_timeout_seconds": 10,
            "cancel_grace_seconds": 1,
            "max_stderr_bytes": 4096,
            "max_event_bytes": 256,
            "max_events": 10,
        },
        "evidence_policy_hash": "sha256:" + "d" * 64,
        "recovery_policy_hash": "sha256:" + "e" * 64,
    }


def _case(
    case_id: str,
    *,
    agent_id: str = "example-agent",
    model: str = "provider/example-model",
    effort: str = "example-effort",
    checker: list[str] | None = None,
) -> dict:
    return {
        "case_id": case_id,
        "request": _request(agent_id=agent_id, model=model, effort=effort),
        "prompt": "write the requested result into the case workspace",
        "task_checker": {
            "argv": checker
            or [sys.executable, "-c", "raise SystemExit(0)"],
            "timeout_seconds": 2,
        },
        "event_constraints": {
            "required_event_types": [],
            "forbidden_event_types": ["permission_violation"],
            "required_permission_decisions": [],
            "forbidden_permission_decisions": [],
        },
    }


def _matrix(*rounds: list[dict], concurrency: int = 2) -> dict:
    selected = list(rounds) or [
        {"round_id": "round-a", "cases": [_case("case-a")]}
    ]
    return {
        "schema_version": 1,
        "server_constraints": {
            "api_version": 3,
            "allowed_daemon_versions": ["test-build"],
        },
        "controller": {
            "max_concurrency": concurrency,
            "max_rounds": len(selected),
            "max_cases": sum(len(item["cases"]) for item in selected),
            "poll_interval_seconds": 0.001,
            "terminal_timeout_seconds": 1,
            "events_page_limit": 100,
            "checker_output_limit_bytes": 4096,
        },
        "rounds": selected,
    }


def _write_matrix(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _Snapshot:
    def __init__(self, *agent_ids: str) -> None:
        self._ids = tuple(agent_ids)

    def ids(self) -> tuple[str, ...]:
        return self._ids


class _FakeClient:
    """Typed-client stand-in with shared observation state."""

    lock = threading.Lock()
    submissions: list[dict] = []
    active_status_calls = 0
    max_active_status_calls = 0
    fail_submit_for: str | None = None
    final_message = "controller-private-final-message"
    checker_delay = 0.02

    @classmethod
    def reset(cls) -> None:
        cls.submissions = []
        cls.active_status_calls = 0
        cls.max_active_status_calls = 0
        cls.fail_submit_for = None

    def __init__(self, socket_path: Path | str, *, api_version: int | None = None):
        self.socket_path = socket_path
        self.api_version = api_version

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def server_info(self) -> dict:
        return {
            "version": "test-build",
            "api_version": 3,
            "supported_api_versions": [3],
            "operations": [
                "server_info",
                "submit",
                "run_status",
                "run_events",
                "session_status",
            ],
            "limits": {
                "max_concurrent_runs": 2,
                "max_run_event_budget_bytes": 10_000,
                "events_page_limit": 100,
            },
        }

    def submit(self, *, request_id: str, payload: dict) -> dict:
        request = payload["request"]
        if request["requested_model"] == self.fail_submit_for:
            raise RuntimeError("synthetic submit refusal")
        with self.lock:
            ordinal = len(self.submissions) + 1
            run_id = f"synthetic-run-{ordinal}"
            session_id = f"synthetic-session-{ordinal}"
            self.submissions.append(
                {
                    "request_id": request_id,
                    "payload": payload,
                    "run_id": run_id,
                    "session_id": session_id,
                }
            )
        return {
            "run_id": run_id,
            "session_id": session_id,
            "accepted_at": "synthetic-time",
        }

    def run_status(self, run_id: str) -> dict:
        client_type = type(self)
        with self.lock:
            client_type.active_status_calls += 1
            client_type.max_active_status_calls = max(
                client_type.max_active_status_calls, client_type.active_status_calls
            )
        time.sleep(self.checker_delay)
        with self.lock:
            client_type.active_status_calls -= 1
        return {
            "run_id": run_id,
            "result": {
                "status": "completed",
                "detail_code": None,
                "retryable": False,
                "final_message": self.final_message,
            },
        }

    def run_events(self, run_id: str, **kwargs) -> dict:
        del kwargs
        return {
            "run_id": run_id,
            "events": [
                {
                    "seq": 1,
                    "type": "permission_mediation",
                    "decision": "allow",
                    "requested_op": "permission:read",
                }
            ],
            "next_from_seq": 1,
            "exhausted": True,
        }

    def session_status(self, session_id: str) -> dict:
        with self.lock:
            row = next(item for item in self.submissions if item["session_id"] == session_id)
        request = row["payload"]["request"]
        return {
            "session_id": session_id,
            "owner": request["owner"],
            "namespace": request["namespace"],
            "agent_id": request["agent_id"],
            "last_effective_model": request["requested_model"],
            "last_effective_effort": request["requested_effort"],
            "quarantine": None,
        }


class _OversizedEventClient(_FakeClient):
    def run_events(self, run_id: str, **kwargs) -> dict:
        del kwargs
        return {
            "run_id": run_id,
            "events": [
                {"seq": 1, "type": "tool_started"},
                {"seq": 2, "type": "tool_finished"},
            ],
            "next_from_seq": 2,
            "exhausted": True,
        }


def _run(tmp_path: Path, payload: dict, **kwargs):
    runner = _runner()
    matrix_path = _write_matrix(tmp_path / "matrix.json", payload)
    output = tmp_path / "evidence"
    result = runner.run_batch(
        matrix_path=matrix_path,
        socket_path=tmp_path / "arsd.sock",
        agents_file=tmp_path / "agents.toml",
        output_dir=output,
        client_factory=_FakeClient,
        registry_loader=lambda path: _Snapshot("example-agent", "other-agent"),
        **kwargs,
    )
    return runner, output, result


def test_required_skill_files_exist() -> None:
    required = {
        "SKILL.md",
        "scripts/run_batch_acceptance.py",
        "scripts/adjudicate.py",
        "references/test-matrix.md",
        "references/evidence-contract.md",
    }
    assert required <= {
        str(path.relative_to(SKILL)) for path in SKILL.rglob("*") if path.is_file()
    }


def test_matrix_parser_rejects_duplicate_and_unknown_keys_before_submission(
    tmp_path: Path,
) -> None:
    runner = _runner()
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(runner.MatrixValidationError):
        runner.load_matrix(duplicate)

    unknown = _matrix()
    unknown["unexpected"] = True
    path = _write_matrix(tmp_path / "unknown.json", unknown)
    with pytest.raises(runner.MatrixValidationError):
        runner.load_matrix(path)


def test_matrix_parser_rejects_non_json_numbers_and_session_reuse(tmp_path: Path) -> None:
    runner = _runner()
    non_json = tmp_path / "constant.json"
    non_json.write_text(
        '{"schema_version":1,"server_constraints":NaN}', encoding="utf-8"
    )
    with pytest.raises(runner.MatrixValidationError):
        runner.load_matrix(non_json)

    payload = _matrix()
    payload["rounds"][0]["cases"][0]["request"]["session_id"] = "existing-session"
    with pytest.raises(runner.MatrixValidationError):
        runner.load_matrix(_write_matrix(tmp_path / "reuse.json", payload))


def test_matrix_requires_explicit_model_and_effort(tmp_path: Path) -> None:
    runner = _runner()
    for field in ("requested_model", "requested_effort"):
        payload = _matrix()
        del payload["rounds"][0]["cases"][0]["request"][field]
        with pytest.raises(runner.MatrixValidationError):
            runner.load_matrix(_write_matrix(tmp_path / f"missing-{field}.json", payload))


def test_preflight_fails_before_output_or_submit_on_live_constraint_mismatch(
    tmp_path: Path,
) -> None:
    _FakeClient.reset()
    runner = _runner()
    payload = _matrix()
    payload["controller"]["max_concurrency"] = 3
    path = _write_matrix(tmp_path / "matrix.json", payload)
    output = tmp_path / "evidence"
    with pytest.raises(runner.PreflightError):
        runner.run_batch(
            matrix_path=path,
            socket_path=tmp_path / "arsd.sock",
            agents_file=tmp_path / "agents.toml",
            output_dir=output,
            client_factory=_FakeClient,
            registry_loader=lambda value: _Snapshot("example-agent"),
        )
    assert not output.exists()
    assert _FakeClient.submissions == []


@pytest.mark.parametrize("mismatch", ["api", "version", "agent", "budget", "matrix"])
def test_each_preflight_dimension_refuses_before_submission(
    tmp_path: Path, mismatch: str
) -> None:
    _FakeClient.reset()
    runner = _runner()
    payload = _matrix()
    snapshot = _Snapshot("example-agent")
    if mismatch == "api":
        payload["server_constraints"]["api_version"] = 2
    elif mismatch == "version":
        payload["server_constraints"]["allowed_daemon_versions"] = ["other-build"]
    elif mismatch == "agent":
        snapshot = _Snapshot("other-agent")
    elif mismatch == "budget":
        payload["rounds"][0]["cases"][0]["request"]["limits"]["max_events"] = 100
    else:
        payload["controller"]["max_cases"] = 0
    with pytest.raises((runner.MatrixValidationError, runner.PreflightError)):
        runner.run_batch(
            matrix_path=_write_matrix(tmp_path / "matrix.json", payload),
            socket_path=tmp_path / "arsd.sock",
            agents_file=tmp_path / "agents.toml",
            output_dir=tmp_path / "evidence",
            client_factory=_FakeClient,
            registry_loader=lambda value: snapshot,
        )
    assert _FakeClient.submissions == []


def test_output_directory_must_not_exist(tmp_path: Path) -> None:
    _FakeClient.reset()
    output = tmp_path / "evidence"
    output.mkdir()
    runner = _runner()
    with pytest.raises(runner.EvidenceError):
        runner.run_batch(
            matrix_path=_write_matrix(tmp_path / "matrix.json", _matrix()),
            socket_path=tmp_path / "arsd.sock",
            agents_file=tmp_path / "agents.toml",
            output_dir=output,
            client_factory=_FakeClient,
            registry_loader=lambda value: _Snapshot("example-agent"),
        )
    assert _FakeClient.submissions == []


def test_rounds_are_sequential_cases_are_capped_and_each_submit_is_fresh(
    tmp_path: Path,
) -> None:
    _FakeClient.reset()
    first_a = _case("case-a")
    first_a["prompt"] = "first-a"
    first_b = _case("case-b")
    first_b["prompt"] = "first-b"
    second_case = _case("case-c")
    second_case["prompt"] = "second"
    first = {
        "round_id": "round-a",
        "cases": [first_a, first_b],
    }
    second = {"round_id": "round-b", "cases": [second_case]}
    _, output, result = _run(tmp_path, _matrix(first, second, concurrency=2))

    assert result["case_count"] == 3
    assert _FakeClient.max_active_status_calls == 2
    request_ids = [item["request_id"] for item in _FakeClient.submissions]
    assert len(request_ids) == len(set(request_ids)) == 3
    assert all("session_id" not in item["payload"]["request"] for item in _FakeClient.submissions)
    submitted_prompts = [
        item["payload"]["prompt_text"] for item in _FakeClient.submissions
    ]
    assert set(submitted_prompts[:2]) == {"first-a", "first-b"}
    assert submitted_prompts[2] == "second"
    assert (output / "controller-manifest.json").is_file()
    assert (output / "completion.json").is_file()
    assert len(list((output / "cases").glob("*.json"))) == 3


def test_submission_failure_is_recorded_once_and_never_retried(tmp_path: Path) -> None:
    _FakeClient.reset()
    _FakeClient.fail_submit_for = "provider/refused-model"
    payload = _matrix(
        {
            "round_id": "round-a",
            "cases": [_case("case-a", model="provider/refused-model")],
        }
    )
    _, output, result = _run(tmp_path, payload)
    assert result["case_count"] == 1
    assert _FakeClient.submissions == []
    evidence = json.loads(next((output / "cases").glob("*.json")).read_text())
    assert evidence["controller"]["submission_attempts"] == 1
    assert evidence["controller"]["submit_error"]["kind"] == "RuntimeError"


def test_checker_runs_as_argv_in_case_workspace_with_timeout_and_bounded_output(
    tmp_path: Path,
) -> None:
    _FakeClient.reset()
    checker = [
        sys.executable,
        "-c",
        "import pathlib,sys; pathlib.Path('checked').write_text('ok'); "
        "sys.stdout.write('x' * 5000)",
    ]
    payload = _matrix(
        {"round_id": "round-a", "cases": [_case("case-a", checker=checker)]}
    )
    _, output, _ = _run(tmp_path, payload)
    case_file = next((output / "cases").glob("*.json"))
    evidence = json.loads(case_file.read_text(encoding="utf-8"))
    checker_result = evidence["controller"]["task_checker"]
    workspace = output / evidence["workspace_relpath"]
    assert (workspace / "checked").read_text(encoding="utf-8") == "ok"
    assert checker_result["returncode"] == 0
    assert checker_result["timed_out"] is False
    assert checker_result["stdout_truncated"] is True
    assert len(checker_result["stdout"].encode("utf-8")) <= 4096


def test_event_capture_is_bounded_by_the_case_event_limit(tmp_path: Path) -> None:
    _OversizedEventClient.reset()
    runner = _runner()
    payload = _matrix()
    payload["rounds"][0]["cases"][0]["request"]["limits"]["max_events"] = 1
    matrix_path = _write_matrix(tmp_path / "matrix.json", payload)
    output = tmp_path / "evidence"
    runner.run_batch(
        matrix_path=matrix_path,
        socket_path=tmp_path / "arsd.sock",
        agents_file=tmp_path / "agents.toml",
        output_dir=output,
        client_factory=_OversizedEventClient,
        registry_loader=lambda path: _Snapshot("example-agent"),
    )
    evidence = json.loads(next((output / "cases").glob("*.json")).read_text())
    assert evidence["controller"]["events_exhausted"] is False
    assert any(
        item["phase"] == "run_events"
        for item in evidence["controller"]["observation_errors"]
    )


def test_adjudication_recomputes_separate_axes_and_sanitizes_receipt(
    tmp_path: Path,
) -> None:
    _FakeClient.reset()
    marker = "private-account-marker"
    case = _case("case-sensitive")
    case["request"]["owner"] = marker
    case["request"]["namespace"] = marker + "-namespace"
    case["request"]["agent_id"] = "example-agent"
    case["request"]["requested_model"] = marker + "-model"
    case["prompt"] = marker + "-raw-prompt"
    case["event_constraints"]["required_event_types"] = ["permission_mediation"]
    case["event_constraints"]["required_permission_decisions"] = ["allow"]
    _, output, _ = _run(tmp_path, _matrix({"round_id": "round-a", "cases": [case]}))

    adjudicator = _adjudicator()
    receipt = adjudicator.build_receipt(output)
    assert receipt["cases"][0]["transport_ars_terminal"]["verdict"] == "PASS"
    assert receipt["cases"][0]["configuration_fidelity"]["verdict"] == "PASS"
    assert receipt["cases"][0]["task_checker"]["verdict"] == "PASS"
    assert receipt["cases"][0]["execution_constraints"]["verdict"] == "PASS"
    assert receipt["cases"][0]["settled_state"]["verdict"] == "PASS"
    assert receipt["cases"][0]["business_verdict"] == "PASS"

    raw_case = json.loads(next((output / "cases").glob("*.json")).read_text())
    encoded = json.dumps(receipt, sort_keys=True)
    for forbidden in (
        marker,
        "example-agent",
        "synthetic-run-",
        "synthetic-session-",
        raw_case["controller"]["request_id"],
        str(tmp_path),
        _FakeClient.final_message,
        "raw-prompt",
        "stderr",
    ):
        assert forbidden not in encoded
    assert "[REDACTED]" in encoded


def test_completed_terminal_does_not_pass_when_checker_or_constraints_fail(
    tmp_path: Path,
) -> None:
    _FakeClient.reset()
    case = _case(
        "case-a",
        checker=[sys.executable, "-c", "raise SystemExit(7)"],
    )
    case["event_constraints"]["forbidden_event_types"] = ["permission_mediation"]
    _, output, _ = _run(tmp_path, _matrix({"round_id": "round-a", "cases": [case]}))
    receipt = _adjudicator().build_receipt(output)
    row = receipt["cases"][0]
    assert row["transport_ars_terminal"]["verdict"] == "PASS"
    assert row["task_checker"]["verdict"] == "FAIL"
    assert row["execution_constraints"]["verdict"] == "FAIL"
    assert row["business_verdict"] == "FAIL"


def test_configuration_fidelity_checks_observable_session_configuration(
    tmp_path: Path,
) -> None:
    _FakeClient.reset()
    _, output, _ = _run(tmp_path, _matrix())
    case_path = next((output / "cases").glob("*.json"))
    evidence = json.loads(case_path.read_text(encoding="utf-8"))
    evidence["controller"]["session_observation"]["agent_id"] = "other-agent"
    case_path.write_text(json.dumps(evidence), encoding="utf-8")

    receipt = _adjudicator().build_receipt(output)
    axis = receipt["cases"][0]["configuration_fidelity"]
    assert axis == {"verdict": "FAIL", "exact_readback": False}
    assert receipt["cases"][0]["business_verdict"] == "FAIL"


def test_adjudication_rejects_non_closed_completion_and_failed_preflight(
    tmp_path: Path,
) -> None:
    _FakeClient.reset()
    adjudicator = _adjudicator()
    _, output, _ = _run(tmp_path, _matrix())
    completion_path = output / "completion.json"
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["unexpected"] = True
    completion_path.write_text(json.dumps(completion), encoding="utf-8")
    with pytest.raises(adjudicator.AdjudicationError):
        adjudicator.build_receipt(output)

    completion.pop("unexpected")
    completion_path.write_text(json.dumps(completion), encoding="utf-8")
    manifest_path = output / "controller-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["preflight"]["checks"]["capacity"] = "FAIL"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(adjudicator.AdjudicationError):
        adjudicator.build_receipt(output)


def test_receipt_refuses_unsafe_case_refs_and_never_projects_unknown_terminals(
    tmp_path: Path,
) -> None:
    _FakeClient.reset()
    adjudicator = _adjudicator()
    _, output, _ = _run(tmp_path, _matrix())
    manifest_path = output / "controller-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["planned_cases"][0]["case_ref"] = "private-account-marker"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(adjudicator.AdjudicationError):
        adjudicator.build_receipt(output)

    manifest["planned_cases"][0]["case_ref"] = "r001-c001"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    case_path = next((output / "cases").glob("*.json"))
    evidence = json.loads(case_path.read_text(encoding="utf-8"))
    marker = "private-terminal-marker"
    evidence["controller"]["terminal_observation"]["result"]["status"] = marker
    case_path.write_text(json.dumps(evidence), encoding="utf-8")
    receipt = adjudicator.build_receipt(output)
    encoded = json.dumps(receipt, sort_keys=True)
    assert marker not in encoded
    assert receipt["cases"][0]["transport_ars_terminal"] == {
        "verdict": "INDETERMINATE",
        "terminal_class": None,
    }


def test_adjudication_refuses_malformed_nested_controller_evidence(
    tmp_path: Path,
) -> None:
    _FakeClient.reset()
    _, output, _ = _run(tmp_path, _matrix())
    case_path = next((output / "cases").glob("*.json"))
    evidence = json.loads(case_path.read_text(encoding="utf-8"))
    evidence["controller"] = []
    case_path.write_text(json.dumps(evidence), encoding="utf-8")
    adjudicator = _adjudicator()
    with pytest.raises(adjudicator.AdjudicationError):
        adjudicator.build_receipt(output)


def test_adjudication_rejects_duplicate_evidence_keys(tmp_path: Path) -> None:
    _FakeClient.reset()
    _, output, _ = _run(tmp_path, _matrix())
    completion_path = output / "completion.json"
    completion_path.write_text(
        '{"schema_version":1,"case_count":1,"case_count":1,'
        '"completed_case_refs":["r001-c001"]}',
        encoding="utf-8",
    )
    adjudicator = _adjudicator()
    with pytest.raises(adjudicator.AdjudicationError):
        adjudicator.build_receipt(output)


def test_receipt_write_is_exclusive(tmp_path: Path) -> None:
    adjudicator = _adjudicator()
    target = tmp_path / "receipt.json"
    target.write_text("existing", encoding="utf-8")
    with pytest.raises(adjudicator.AdjudicationError):
        adjudicator.write_receipt(target, {"schema_version": 1})
    assert target.read_text(encoding="utf-8") == "existing"


def test_script_and_skill_hygiene() -> None:
    run_source = RUNNER_PATH.read_text(encoding="utf-8")
    adjudicate_source = ADJUDICATOR_PATH.read_text(encoding="utf-8")
    skill_source = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    matrix_reference = (SKILL / "references" / "test-matrix.md").read_text(
        encoding="utf-8"
    )
    evidence_reference = (SKILL / "references" / "evidence-contract.md").read_text(
        encoding="utf-8"
    )

    assert "ArsdClient" in run_source
    assert "load_agents_file" in run_source
    assert "subprocess.run" in run_source
    assert "shell=True" not in run_source
    assert "socket.socket" not in run_source
    assert "bubble" not in run_source.lower()
    for banned in ("/proc", "systemctl", "journalctl"):
        assert banned not in run_source
        assert banned not in adjudicate_source

    assert skill_source.startswith(
        "---\nname: ars-batch-agent-acceptance\ndescription:"
    )
    assert "references/test-matrix.md" in skill_source
    assert "references/evidence-contract.md" in skill_source
    assert "completed" in skill_source and "trusted checker" in skill_source
    assert "response-only" in skill_source
    assert "permissions" in skill_source
    assert "session reuse" in skill_source.lower()
    assert "bubble sort" in matrix_reference.lower()
    assert "read/search allow" in matrix_reference.lower()
    assert "execute allow" in matrix_reference.lower()
    assert "write/edit deny" in matrix_reference.lower()
    assert "write/edit allow" not in matrix_reference.lower()
    assert "session reuse" in matrix_reference.lower()
    assert "raw evidence" in evidence_reference.lower()
    assert "[REDACTED]" in evidence_reference

    authored = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SKILL.rglob("*")
        if path.is_file() and path.suffix in {".md", ".py"}
    )
    for host_specific in (
        "/" + "home" + "/",
        "/" + "Users" + "/",
        "workspace" + "/" + "hermes",
        "github" + ".com/",
    ):
        assert host_specific not in authored

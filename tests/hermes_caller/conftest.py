"""Shared fakes and fixtures for the offline Hermes caller test suite.

IMPORTANT (TDD RED stage): this conftest deliberately imports **only** real,
already-shipped supervisor modules (``caller``, ``role``, ``result``,
``exit_classifier``). It must **never** import the not-yet-implemented
``agent_run_supervisor.hermes_caller`` package — doing so would turn every
test in this directory into a conftest collection error instead of the clean,
per-module ``ModuleNotFoundError`` that is the expected RED signal. Each test
module imports the production package itself.

Everything here is fake/local/offline: canned ``RunOutcome`` / session outcomes
fed to ``invoke_caller`` through its injected ``runner`` / ``session_runtime``
parameters, plus synthetic redacted artifacts. No acpx, no network, no Feishu,
no delivery, no Sachima.
"""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from agent_run_supervisor.caller import CallerResult
from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.result import RunOutcome
from agent_run_supervisor.role import AgentRoleSpec, load_role
from agent_run_supervisor.runner import DryRunResult

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
EVENTS_FILE_NAME = "normalized-events.jsonl"


# --------------------------------------------------------------------------- #
# Fixture loading helpers
# --------------------------------------------------------------------------- #
def _load_json(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture()
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture()
def load_result() -> Callable[[str], dict[str, Any]]:
    """Return a loader for a synthetic redacted ``result.json`` fixture."""
    return _load_json


@pytest.fixture()
def run_result_pass() -> dict[str, Any]:
    return _load_json("run_result_pass.json")


@pytest.fixture()
def run_result_issues() -> dict[str, Any]:
    return _load_json("run_result_issues.json")


@pytest.fixture()
def run_result_failed() -> dict[str, Any]:
    return _load_json("run_result_failed.json")


def _materialize_events(run_dir: Path, events_fixture: str) -> Path:
    """Copy a synthetic event stream into ``run_dir`` as normalized-events.jsonl."""
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES_DIR / events_fixture, run_dir / EVENTS_FILE_NAME)
    return run_dir


@pytest.fixture()
def events_run_dir(tmp_path: Path) -> Path:
    """A local run/turn artifact dir carrying a successful normalized event stream."""
    return _materialize_events(tmp_path / "run-success", "normalized_events_success.jsonl")


@pytest.fixture()
def failed_events_run_dir(tmp_path: Path) -> Path:
    """A local run/turn artifact dir carrying a failed normalized event stream."""
    return _materialize_events(tmp_path / "run-failed", "normalized_events_failed.jsonl")


# --------------------------------------------------------------------------- #
# Caller-side document-check inputs (raw kwargs; tests build the DocCheckTask)
# --------------------------------------------------------------------------- #
DOC_BODY_SENTINEL = "DOC_BODY_SENTINEL caller-owned check material [REDACTED]"


@pytest.fixture()
def doc_body_sentinel() -> str:
    """Sentinel string embedded in the local document-under-check fixture.

    Exposed as a fixture (not a cross-module import) because ``tests/`` is not a
    Python package here, so ``from .conftest import ...`` would not resolve.
    """
    return DOC_BODY_SENTINEL


@pytest.fixture()
def doc_file(tmp_path: Path) -> Path:
    path = tmp_path / "doc-under-check.md"
    path.write_text(
        "# Document Under Check\n\n" + DOC_BODY_SENTINEL + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def doc_task_kwargs(doc_file: Path) -> dict[str, Any]:
    """Constructor kwargs for a DocCheckTask.

    All caller-side correlation/identity values are redacted; none of these may
    ever reach the supervisor as platform fields.
    """
    return {
        "task_id": "[REDACTED]",
        "document_ref": str(doc_file),
        "check_profile": "completeness+xref",
        "requested_by": "[REDACTED]",
        "surface": "feishu_card",
    }


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #
def _role(valid_role_dict: dict[str, Any], work_dir: Path, strategy: str) -> AgentRoleSpec:
    payload = copy.deepcopy(valid_role_dict)
    payload["role_id"] = "doc-check-reviewer"
    payload["workspace"]["default_cwd"] = str(work_dir)
    payload["workspace"]["allowed_roots"] = [str(work_dir)]
    payload["runner"]["acpx_binary"] = str(work_dir / "fake-acpx")
    payload["session"] = {"strategy": strategy}
    return load_role(payload)


@pytest.fixture()
def work_dir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture()
def exec_doc_role(valid_role_dict: dict[str, Any], work_dir: Path) -> AgentRoleSpec:
    return _role(valid_role_dict, work_dir, "exec")


@pytest.fixture()
def persistent_doc_role(valid_role_dict: dict[str, Any], work_dir: Path) -> AgentRoleSpec:
    return _role(valid_role_dict, work_dir, "persistent")


# --------------------------------------------------------------------------- #
# CallerResult builder
# --------------------------------------------------------------------------- #
@pytest.fixture()
def make_caller_result() -> Callable[..., CallerResult]:
    def _make(
        result: dict[str, Any],
        *,
        mode: str = "exec",
        run_dir: str | None = None,
        session_dir: str | None = None,
    ) -> CallerResult:
        status = result.get("status")
        return CallerResult(
            mode=mode,
            supervisor_status=status if isinstance(status, str) else None,
            result=dict(result),
            artifact_dir=run_dir,
            run_dir=run_dir,
            session_dir=session_dir,
            business_verdict=None,
        )

    return _make


# --------------------------------------------------------------------------- #
# Fake runner (exec / exec_dry_run) injected into invoke_caller
# --------------------------------------------------------------------------- #
class FakeRunner:
    """Stand-in for ``SupervisorRunner`` used by ``invoke_caller``.

    Records each ``run`` / ``dry_run`` call and returns a canned outcome. It
    never launches a subprocess, so a ``dry_run`` truly launches nothing.
    """

    def __init__(
        self,
        *,
        run_result: dict[str, Any],
        run_dir: Path,
        dry_run_result: dict[str, Any],
        dry_run_dir: Path,
    ) -> None:
        self._run_result = run_result
        self._run_dir = run_dir
        self._dry_run_result = dry_run_result
        self._dry_run_dir = dry_run_dir
        self.run_calls: list[dict[str, Any]] = []
        self.dry_run_calls: list[dict[str, Any]] = []

    def run(self, *, role: AgentRoleSpec, prompt: str, cwd: str | None, **_: Any) -> RunOutcome:
        self.run_calls.append({"role": role, "prompt": prompt, "cwd": cwd})
        return RunOutcome(
            run_dir=self._run_dir,
            status=AgentRunStatus.COMPLETED,
            result=dict(self._run_result),
        )

    def dry_run(self, *, role: AgentRoleSpec, prompt: str, cwd: str | None, **_: Any) -> DryRunResult:
        self.dry_run_calls.append({"role": role, "prompt": prompt, "cwd": cwd})
        return DryRunResult(
            run_id="[REDACTED]",
            run_dir=self._dry_run_dir,
            result=dict(self._dry_run_result),
        )


@pytest.fixture()
def fake_runner(
    run_result_pass: dict[str, Any],
    events_run_dir: Path,
    tmp_path: Path,
) -> FakeRunner:
    dry_dir = tmp_path / "run-dry"
    dry_dir.mkdir(parents=True, exist_ok=True)
    dry_result = dict(run_result_pass)
    dry_result["status"] = "dry_run"
    dry_result["detail_code"] = "DRY_RUN"
    dry_result["error_code"] = "DRY_RUN"
    dry_result["final_message"] = ""
    return FakeRunner(
        run_result=run_result_pass,
        run_dir=events_run_dir,
        dry_run_result=dry_result,
        dry_run_dir=dry_dir,
    )


# --------------------------------------------------------------------------- #
# Fake session runtime (create -> send* -> status -> close) for invoke_caller
# --------------------------------------------------------------------------- #
class FakeSessionRuntime:
    """Stand-in for ``SessionRuntime`` used by ``invoke_caller``.

    ``invoke_caller`` only reads ``.result`` plus a ``.session_dir`` / ``.turn_dir``
    off each outcome, so lightweight namespaces suffice. Call order is recorded
    so the orchestration flow can be asserted: create -> send xN -> status -> close.
    """

    def __init__(
        self,
        *,
        sessions_dir: Path,
        session_dir: Path,
        turn_dir: Path,
        create_result: dict[str, Any],
        turn_results: list[dict[str, Any]],
        status_result: dict[str, Any],
        close_result: dict[str, Any],
    ) -> None:
        self.sessions_dir = Path(sessions_dir)
        self._session_dir = session_dir
        self._turn_dir = turn_dir
        self._create_result = create_result
        self._turn_results = list(turn_results)
        self._status_result = status_result
        self._close_result = close_result
        self.calls: list[str] = []
        self._sent = 0

    def create_session(self, *, role: AgentRoleSpec, session_id: str, **_: Any) -> SimpleNamespace:
        self.calls.append("create_session")
        return SimpleNamespace(result=dict(self._create_result), session_dir=self._session_dir)

    def send(self, *, role: AgentRoleSpec, session_id: str, prompt: str, **_: Any) -> SimpleNamespace:
        self.calls.append("send")
        idx = min(self._sent, len(self._turn_results) - 1)
        self._sent += 1
        result = dict(self._turn_results[idx])
        result["run_dir"] = str(self._turn_dir)
        return SimpleNamespace(result=result, turn_dir=self._turn_dir)

    def status(self, *, role: AgentRoleSpec, session_id: str, **_: Any) -> SimpleNamespace:
        self.calls.append("status")
        return SimpleNamespace(result=dict(self._status_result))

    def close(self, *, role: AgentRoleSpec, session_id: str, **_: Any) -> SimpleNamespace:
        self.calls.append("close")
        return SimpleNamespace(result=dict(self._close_result))


@pytest.fixture()
def fake_session_runtime(
    tmp_path: Path,
    run_result_pass: dict[str, Any],
    events_run_dir: Path,
) -> FakeSessionRuntime:
    sessions_dir = tmp_path / "sessions"
    session_dir = sessions_dir / "[REDACTED]"
    create_result = {
        "session_id": "[REDACTED]",
        "acpx_session_id": "[REDACTED]",
        "session_name": "[REDACTED]",
        "state": "open",
        "kind": "session_ensured",
        "created": True,
        "session_dir": str(session_dir),
        "business_verdict": None,
    }
    turn_result = dict(run_result_pass)
    turn_result["session_id"] = "[REDACTED]"
    turn_result["turn_id"] = "[REDACTED]"
    status_result = {
        "session_id": "[REDACTED]",
        "ok": True,
        "acpx_exit_code": 0,
        "summary": {"kind": "status_snapshot", "status": "alive"},
        "business_verdict": None,
    }
    close_result = {
        "session_id": "[REDACTED]",
        "state": "closed",
        "closed": True,
        "kind": "session_closed",
        "acpx_session_id": "[REDACTED]",
        "business_verdict": None,
    }
    return FakeSessionRuntime(
        sessions_dir=sessions_dir,
        session_dir=session_dir,
        turn_dir=events_run_dir,
        create_result=create_result,
        turn_results=[turn_result, turn_result],
        status_result=status_result,
        close_result=close_result,
    )

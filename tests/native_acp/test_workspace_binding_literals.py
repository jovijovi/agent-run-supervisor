"""The workspace binding fields are complete literals, and hash-covered.

The canonical workspace root and the effective ``cwd`` are independently
derived authority facts: the caller binds a workspace, ARS canonicalizes it,
and reconciliation, audit, and Session identity all depend on the exact
literal. They stay complete in ``spec.json`` and stay covered by ``spec_hash``
— including when the workspace lives under ``$HOME`` and therefore shares bytes
with a projected environment value.

This used to be an *exemption* from the per-Run literal guard. With that guard
removed the property is no longer an exemption from anything, but it is still
load-bearing and still worth a test: a future truncation, tokenisation, or
"tidy up the paths" change would break workspace binding, reconciliation
attribution, and the seal, and would break here first.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp.profile import ProfileRegistry
from agent_run_supervisor.native_acp.spec import spec_hash_of_payload

from .test_run_task import HAPPY_SCRIPT, Harness, _run, _test_profile

ALLOWLIST = ("PATH", "HOME", "FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE")


@pytest.fixture()
def home_rooted_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A Run whose workspace really is under ``$HOME``, with ``HOME`` projected."""
    # The canonical workspace root is a resolved path, so ``HOME`` has to be
    # the resolved one too or the two would only look related.
    home = str(tmp_path.resolve())
    monkeypatch.setenv("HOME", home)
    script = dict(HAPPY_SCRIPT)
    script["echo_env"] = "HOME"
    harness = Harness(tmp_path, monkeypatch, script)
    harness.registry = ProfileRegistry((_test_profile(base_allowlist=ALLOWLIST),))
    result = _run(harness.task())
    assert result.status is AgentRunStatus.COMPLETED
    return harness, home


def test_spec_workspace_fields_keep_their_complete_literal_under_home(
    home_rooted_run,
) -> None:
    harness, home = home_rooted_run
    spec = json.loads((harness.run_dir() / "spec.json").read_text())

    workspace = spec["workspace"]
    assert workspace["canonical_root"] == str(harness.workspace.resolve())
    assert workspace["cwd"] == str(harness.workspace.resolve())
    # Complete, not truncated and not tokenized.
    assert workspace["canonical_root"].startswith(home)


def test_workspace_fields_stay_covered_by_the_spec_hash(home_rooted_run) -> None:
    harness, _home = home_rooted_run
    payload = json.loads((harness.run_dir() / "spec.json").read_text())

    # The durable seal verifies against the durable document.
    assert spec_hash_of_payload(payload) == payload["spec_hash"]

    for field in ("canonical_root", "cwd", "workspace_hash"):
        mutated = json.loads(json.dumps(payload))
        mutated["workspace"][field] = mutated["workspace"][field] + "-tampered"
        assert spec_hash_of_payload(mutated) != payload["spec_hash"], field


def test_environment_values_still_never_enter_the_sealed_material(
    home_rooted_run,
) -> None:
    """The structural half of the environment boundary, which did not move.

    ``launch.json`` records the projected **names**, their source class, their
    precedence layer, and their redaction status. The workspace literal above
    is in ``spec.json`` because it is a workspace fact, not because a value
    leaked into the seal.
    """
    harness, _home = home_rooted_run
    launch = json.loads((harness.run_dir() / "launch.json").read_text())

    assert launch["env"]["values_persisted"] is False
    assert "HOME" in {item["name"] for item in launch["env"]["names"]}
    for item in launch["env"]["names"]:
        assert set(item) == {"name", "source", "precedence", "redacted"}

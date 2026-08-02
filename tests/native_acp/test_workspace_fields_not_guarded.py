"""Reviewer note 5, encoded as a test rather than a comment.

The workspace canonical root and the effective ``cwd`` are **not** environment
values. They are independently derived authority facts: the caller binds a
workspace, ARS canonicalizes it, and reconciliation, audit, and Session
identity all depend on the exact literal. They stay complete in ``spec.json``
and stay covered by ``spec_hash`` — even when the workspace lives under
``$HOME`` and therefore shares bytes with a projected environment value.

Shared bytes are not shared provenance. The boundary is proven by taint-
directed call paths, not by lexical coincidence: ``spec.json`` is sealed before
the environment is ever resolved, so no guarded value can flow into it, while
the *same* Run's child-authored final message is guarded in the same assertion
block. One Run, both halves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp.profile import ProfileRegistry
from agent_run_supervisor.native_acp.spec import spec_hash_of_payload
from agent_run_supervisor.redaction import ENV_VALUE_REPLACEMENT, RunTextGuard

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
    # The contrast half: the child echoes the very same value back through a
    # sink, in the same Run, from the same literal set.
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
    assert ENV_VALUE_REPLACEMENT not in json.dumps(workspace)


def test_the_same_value_is_still_guarded_in_child_authored_text(
    home_rooted_run,
) -> None:
    harness, home = home_rooted_run
    payload = json.loads((harness.run_dir() / "result.json").read_text())

    # Same Run, same literal set, opposite outcome — because the provenance is
    # opposite, not because the bytes are.
    assert payload["final_message"] == f"ENV:{ENV_VALUE_REPLACEMENT}"
    assert home not in payload["final_message"]


def test_workspace_fields_stay_covered_by_the_spec_hash(home_rooted_run) -> None:
    harness, _home = home_rooted_run
    payload = json.loads((harness.run_dir() / "spec.json").read_text())

    # The durable seal verifies against the durable document.
    assert spec_hash_of_payload(payload) == payload["spec_hash"]

    for field in ("canonical_root", "cwd", "workspace_hash"):
        mutated = json.loads(json.dumps(payload))
        mutated["workspace"][field] = mutated["workspace"][field] + "-tampered"
        assert spec_hash_of_payload(mutated) != payload["spec_hash"], field


def test_guarding_the_workspace_fields_would_have_been_visible() -> None:
    """The exemption is load-bearing, not incidental.

    If ``spec.json`` were routed through the guard, a workspace under ``$HOME``
    would lose its root — this asserts the guard really would have destroyed
    it, so the exemption above is a decision and not an accident of ordering.
    """
    home = "/home/someone"
    guard = RunTextGuard.from_environment({"HOME": home})

    assert guard.guard_text(f"{home}/project/src") == (
        f"{ENV_VALUE_REPLACEMENT}/project/src"
    )

"""Focused Codex ACP grant-driven permission-mode compatibility tests.

The registered profile keeps standard ACP v1 and separate model/effort
selectors, but derives the required mode from each Run's frozen grant.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp.agent_registration import AgentEntry
from agent_run_supervisor.native_acp.config_fidelity import ConfigFidelityError
from agent_run_supervisor.native_acp.profile import (
    CLAUDE_AGENT_ACP_COMPAT_V1,
    CURSOR_NATIVE_ACP_V1,
    DEFAULT_REGISTRY,
    STANDARD_NATIVE_ACP_V1,
    ProfileRegistry,
)
from agent_run_supervisor.native_acp.run_task import DISPATCH_STARTED_MARKER
from agent_run_supervisor.native_acp.spec import PERMISSION_KINDS, NativeSpecError

from .test_run_task import FAKE_AGENT_PATH, Harness, _request, _run

REGISTERED_PROFILE_ID = "codex-agent-acp-compat-v1"
CODEX_AGENT_ID = "codex-registered"
POLICY_ID = "read-only-grant-read-only-else-agent-v1"

INITIAL_MODEL = "provider/base"
REQUESTED_MODEL = "provider/codex-model"
INITIAL_EFFORT = "high"
REQUESTED_EFFORT = "max"
READ_ONLY_CAPABILITIES = frozenset({"read", "search"})

STANDARD_HASH = "fcf4d46c2c072ba9bd23b198beb096cb9748e62e8168c2a48e5c76432d55f9b9"
CLAUDE_HASH = "c9e9258bfcc01e2962b87466c803d0a3ae25a1676936864bdbd78b75a544a241"
CURSOR_HASH = "9ec329a6ac5844ea9df789344fbaeeab7ec2cca7b704da66f470a118a68063e4"
CODEX_HASH = "de3c26137e30319336c271710d47e235fd895ce43253c364782f6b007900b309"


def _profile():
    return DEFAULT_REGISTRY.get(REGISTERED_PROFILE_ID)


def _all_valid_grants():
    for size in range(len(PERMISSION_KINDS) + 1):
        yield from itertools.combinations(PERMISSION_KINDS, size)


def _mode_option(current: str, *, advertised=None) -> dict:
    values = advertised or ("read-only", "agent", "agent-full-access")
    return {
        "id": "mode",
        "name": "Mode",
        "type": "select",
        "currentValue": current,
        "options": [{"value": value, "name": value} for value in values],
    }


def _options(
    mode_current: str,
    *,
    model_current: str = INITIAL_MODEL,
    effort_current: str = INITIAL_EFFORT,
) -> list[dict]:
    return [
        {
            "id": "model",
            "name": "Model",
            "type": "select",
            "currentValue": model_current,
            "options": [
                {"value": INITIAL_MODEL, "name": "Base"},
                {"value": REQUESTED_MODEL, "name": "Codex model"},
            ],
        },
        {
            "id": "effort",
            "name": "Effort",
            "type": "select",
            "currentValue": effort_current,
            "options": [
                {"value": INITIAL_EFFORT, "name": "High"},
                {"value": REQUESTED_EFFORT, "name": "Max"},
            ],
        },
        _mode_option(mode_current),
    ]


def _script_for(required_mode: str, *, initial_mode: str) -> dict:
    return {
        "initial_options": _options(initial_mode),
        "post_model_options_by_value": {
            REQUESTED_MODEL: _options(
                required_mode,
                model_current=REQUESTED_MODEL,
            )
        },
        "final_message": "CODEX_OK",
    }


def _entry() -> AgentEntry:
    return AgentEntry(
        agent_id=CODEX_AGENT_ID,
        profile_id=REGISTERED_PROFILE_ID,
        command=sys.executable,
        args=(str(FAKE_AGENT_PATH),),
        env_passthrough=("FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"),
    )


def _registered_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: dict
) -> Harness:
    harness = Harness(tmp_path, monkeypatch, script)
    harness.registry = ProfileRegistry((_profile(),))
    harness.entry = _entry()
    return harness


def _create_request(grant=("read",), **overrides):
    kwargs = dict(
        agent_id=CODEX_AGENT_ID,
        session_id=None,
        requested_model=REQUESTED_MODEL,
        requested_effort=REQUESTED_EFFORT,
        grant_capabilities=tuple(grant),
    )
    kwargs.update(overrides)
    return _request(**kwargs)


def _reuse_request(session_id: str, grant=("read",), **overrides):
    return _create_request(grant=grant, session_id=session_id, **overrides)


def _result_payload(harness: Harness, run_id: str = "run-0001") -> dict:
    return json.loads((harness.run_dir(run_id) / "result.json").read_text())


def _effective(harness: Harness, run_id: str = "run-0001") -> dict:
    return json.loads((harness.run_dir(run_id) / "effective.json").read_text())


def _snapshot_mode(effective: dict, label: str):
    rows = [
        row for row in effective["discovery_snapshots"] if row["label"] == label
    ]
    assert len(rows) == 1, label
    option = next(
        (item for item in rows[0]["options"] if item["id"] == "mode"), None
    )
    return None if option is None else option.get("currentValue")


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def _event_types(harness: Harness, run_id: str) -> list[str]:
    path = harness.run_dir(run_id) / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line)["type"] for line in path.read_text().splitlines()]


def test_profile_is_registered_with_exact_hash_relevant_semantics() -> None:
    profile = _profile()
    expected = STANDARD_NATIVE_ACP_V1.snapshot()
    expected.update(
        profile_id=REGISTERED_PROFILE_ID,
        revision=1,
        permission_mode_selector_id="mode",
        permission_mode_policy_id=POLICY_ID,
    )

    assert profile.snapshot() == expected
    assert profile.profile_hash() == CODEX_HASH
    assert profile.snapshot_ref() == f"registry:{REGISTERED_PROFILE_ID}@r1"
    assert profile.config_fidelity_mode == "separate-selectors"
    assert profile.model_selector_id == "model"
    assert profile.effort_selector_id == "effort"
    assert profile.required_permission_mode is None
    assert profile.session_meta is None


def test_existing_profile_hashes_do_not_move() -> None:
    assert STANDARD_NATIVE_ACP_V1.profile_hash() == STANDARD_HASH
    assert CLAUDE_AGENT_ACP_COMPAT_V1.profile_hash() == CLAUDE_HASH
    assert CURSOR_NATIVE_ACP_V1.profile_hash() == CURSOR_HASH


def test_all_read_only_grant_subsets_require_read_only() -> None:
    for grant in ((), ("read",), ("search",), ("read", "search")):
        assert _profile().required_permission_mode_for(grant) == "read-only"


def test_every_valid_grant_with_an_outside_capability_requires_agent() -> None:
    checked = 0
    for grant in _all_valid_grants():
        if set(grant) <= READ_ONLY_CAPABILITIES:
            continue
        assert _profile().required_permission_mode_for(grant) == "agent", grant
        checked += 1
    assert checked == (2 ** len(PERMISSION_KINDS)) - 4


def test_agent_full_access_is_unreachable_from_the_policy() -> None:
    outputs = {
        _profile().required_permission_mode_for(grant)
        for grant in _all_valid_grants()
    }
    assert outputs == {"read-only", "agent"}
    assert "agent-full-access" not in outputs


@pytest.mark.parametrize(
    "malformed",
    [None, 7, "read", ("read", "not-a-permission")],
)
def test_malformed_grants_fail_at_the_shared_request_boundary(malformed) -> None:
    with pytest.raises((NativeSpecError, TypeError)):
        _request(grant_capabilities=malformed)


@pytest.mark.parametrize("malformed", [None, 7, "read", b"read"])
def test_policy_entry_refuses_non_iterable_or_text_inputs(malformed) -> None:
    with pytest.raises((ConfigFidelityError, TypeError)):
        _profile().required_permission_mode_for(malformed)



def test_driver_sets_mode_before_model_and_keeps_it_exact_through_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs = tmp_path / "config.log"
    script = _script_for("read-only", initial_mode="agent-full-access")
    script["capture_config_path"] = str(configs)
    harness = _registered_harness(tmp_path, monkeypatch, script)

    result = _run(harness.task(request=_create_request(grant=("read", "search"))))

    assert result.status is AgentRunStatus.COMPLETED, _result_payload(harness)
    assert _lines(configs) == [
        "mode=read-only",
        f"model={REQUESTED_MODEL}",
        f"effort={REQUESTED_EFFORT}",
    ]
    methods = harness.methods_seen()
    set_indexes = [
        index
        for index, method in enumerate(methods)
        if method == "session/set_config_option"
    ]
    assert len(set_indexes) == 3
    assert max(set_indexes) < methods.index("session/prompt")

    effective = _effective(harness)
    assert [row["label"] for row in effective["discovery_snapshots"]] == [
        "initial",
        "post_mode",
        "post_model",
        "post_effort",
    ]
    assert _snapshot_mode(effective, "initial") == "agent-full-access"
    for label in ("post_mode", "post_model", "post_effort"):
        assert _snapshot_mode(effective, label) == "read-only"


@pytest.mark.parametrize(
    ("label", "mutation"),
    [
        ("missing mode selector", "missing-selector"),
        ("missing required current value", "missing-current"),
        ("required value unadvertised", "unadvertised"),
        ("inexact readback", "inexact"),
    ],
)
def test_mode_fidelity_failures_are_config_fidelity_before_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    mutation: str,
) -> None:
    script = _script_for("read-only", initial_mode="agent-full-access")
    if mutation == "missing-selector":
        script["initial_options"] = [
            option for option in script["initial_options"] if option["id"] != "mode"
        ]
    elif mutation == "missing-current":
        script["wrong_readback"] = {"mode": None}
    elif mutation == "unadvertised":
        script["initial_options"][-1] = _mode_option(
            "agent-full-access", advertised=("agent", "agent-full-access")
        )
    else:
        script["wrong_readback"] = {"mode": "agent"}
    harness = _registered_harness(tmp_path, monkeypatch, script)

    result = _run(harness.task(request=_create_request(grant=("read",))))

    assert result.status is AgentRunStatus.FAILED, label
    assert _result_payload(harness)["detail_code"] == "CONFIG_FIDELITY", label
    assert "session/prompt" not in harness.methods_seen(), label
    assert not (harness.run_dir() / DISPATCH_STARTED_MARKER).exists(), label


def test_new_and_real_load_runs_recompute_mode_from_their_own_frozen_grants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs = tmp_path / "config.log"
    first_script = _script_for("read-only", initial_mode="agent-full-access")
    first_script["capture_config_path"] = str(configs)
    harness = _registered_harness(tmp_path, monkeypatch, first_script)

    first = _run(
        harness.task(run_id="run-0001", request=_create_request(grant=("read",)))
    )
    assert first.status is AgentRunStatus.COMPLETED, _result_payload(harness)
    assert first.session_id

    second_script = _script_for("agent", initial_mode="read-only")
    second_script["capture_config_path"] = str(configs)
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps(second_script))
    second = _run(
        harness.task(
            run_id="run-0002",
            request=_reuse_request(first.session_id, grant=("read", "write")),
            seed_session=False,
        )
    )

    assert second.status is AgentRunStatus.COMPLETED, _result_payload(
        harness, "run-0002"
    )
    assert _lines(configs) == [
        "mode=read-only",
        f"model={REQUESTED_MODEL}",
        f"effort={REQUESTED_EFFORT}",
        "mode=agent",
        f"model={REQUESTED_MODEL}",
        f"effort={REQUESTED_EFFORT}",
    ]
    event_types = _event_types(harness, "run-0002")
    assert "session_load_requested" in event_types
    assert "session_new_requested" not in event_types
    second_effective = _effective(harness, "run-0002")
    assert _snapshot_mode(second_effective, "initial") == "read-only"
    for label in ("post_mode", "post_model", "post_effort"):
        assert _snapshot_mode(second_effective, label) == "agent"

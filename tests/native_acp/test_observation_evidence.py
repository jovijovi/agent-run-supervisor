"""A11 — observations are evidence, never gates.

After a successful spawn ARS records the declared command and exact argv, the
agent's self-reported name and version, the protocol version, the advertised
capabilities, and any resolution facts it can compute. **Every one is marked
non-authoritative.** No code path compares any of them against a source
constant, a prior Run, a Session record, or a registry value to decide admission
or reuse.

A self-report is not an identity in either direction: a substituted agent can
report any name it likes, and an operator-declared expected name would refuse
Runs for cosmetic vendor renames.

The complete set of observation-based refusals is exactly five: protocol major
mismatch, a required capability absent, a forbidden capability present, an
inexact or coerced configuration readback, and — on a compatibility profile — a
required permission mode not proven by readback. Those are checks against a
declared contract inside one Run, not continuity comparisons.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from agent_run_supervisor.native_acp import observation, run_task
from agent_run_supervisor.native_acp.agent_registration import AgentEntry
from agent_run_supervisor.native_acp.profile import (
    CLAUDE_AGENT_ACP_COMPAT_V1,
    STANDARD_NATIVE_ACP_V1,
    AgentInstance,
)

RUN_TASK_SOURCE = Path(run_task.__file__)
OBSERVATION_SOURCE = Path(observation.__file__)


def instance(profile=STANDARD_NATIVE_ACP_V1, **overrides) -> AgentInstance:
    body = {
        "agent_id": "a-1",
        "profile_id": profile.profile_id,
        "command": "some-agent",
    }
    body.update(overrides)
    return AgentInstance(profile, AgentEntry(**body))


def summary(**overrides):
    body = {
        "agent_info": {"name": "SomeAgent", "version": "9.9.9"},
        "protocol_version": 1,
        "capabilities": {"loadSession": True},
        "load_session_advertised": True,
    }
    body.update(overrides)
    return observation.InitializeObservation(**body)


# -- the five refusals, and only those ---------------------------------------


def test_a_conforming_initialize_passes():
    verdict = observation.judge_initialize(instance(), summary())
    assert verdict.refusal is None
    assert verdict.warnings == ()


def test_protocol_major_mismatch_refuses():
    verdict = observation.judge_initialize(instance(), summary(protocol_version=2))
    assert verdict.refusal == "PROTOCOL_MISMATCH"


def test_a_missing_required_capability_refuses():
    verdict = observation.judge_initialize(
        instance(), summary(capabilities={}, load_session_advertised=False)
    )
    assert verdict.refusal == "CAPABILITY_MISSING"


def test_a_forbidden_capability_present_refuses():
    verdict = observation.judge_initialize(
        instance(forbidden_capabilities=("terminal",)),
        summary(capabilities={"loadSession": True, "terminal": True}),
    )
    assert verdict.refusal == "CAPABILITY_FORBIDDEN"


def test_the_refusal_set_is_closed_and_named():
    assert observation.OBSERVATION_REFUSALS == (
        "PROTOCOL_MISMATCH",
        "CAPABILITY_MISSING",
        "CAPABILITY_FORBIDDEN",
        "CONFIG_FIDELITY",
        "PERMISSION_MODE_UNPROVEN",
    )


# -- what is no longer a gate ------------------------------------------------


@pytest.mark.parametrize(
    "agent_info",
    [
        {"name": "SomethingElse", "version": "9.9.9"},
        {"name": "SomeAgent", "version": "0.0.1"},
        {},
        {"name": "", "version": ""},
        None,
    ],
)
def test_agent_info_never_refuses(agent_info):
    """A self-report is evidence. A vendor rename is a non-event for ARS."""
    verdict = observation.judge_initialize(instance(), summary(agent_info=agent_info))
    assert verdict.refusal is None


def test_agent_info_drift_between_two_runs_warns_and_never_refuses():
    """A1: one unchanged registered command, an upgraded agent behind it."""
    first = observation.judge_initialize(
        instance(), summary(agent_info={"name": "SomeAgent", "version": "1.0.0"})
    )
    second = observation.judge_initialize(
        instance(),
        summary(agent_info={"name": "SomeAgent", "version": "2.0.0"}),
        previous=first.observed,
    )
    assert second.refusal is None
    assert any(
        warning["code"] == "AGENT_SELF_REPORT_CHANGED" for warning in second.warnings
    )


def test_capability_drift_between_two_runs_warns_and_never_refuses():
    first = observation.judge_initialize(instance(), summary())
    second = observation.judge_initialize(
        instance(),
        summary(capabilities={"loadSession": True, "fs": True}),
        previous=first.observed,
    )
    assert second.refusal is None
    assert any(
        warning["code"] == "ADVERTISED_CAPABILITIES_CHANGED"
        for warning in second.warnings
    )


# -- B3: one caller-facing policy-warning shape -------------------------------
#
# ``docs/design/result-event-schema.md`` §5.5 is the caller-facing authority for
# this family, and a caller parses what the schema documents. The emitted record
# and the documented record therefore have to be the same record — one field
# vocabulary, closed categorical values, and nothing that says what an observed
# fact *was*.

POLICY_WARNING_FIELDS = {
    "type",
    "code",
    "subject",
    "comparison",
    "authoritative",
    "refused",
}


def _drifted_warnings():
    """Both warning codes, from one pair of Runs of one Session."""
    first = observation.judge_initialize(
        instance(), summary(agent_info={"name": "SomeAgent", "version": "1.0.0"})
    )
    second = observation.judge_initialize(
        instance(),
        summary(
            agent_info={"name": "Renamed", "version": "2.0.0"},
            capabilities={"loadSession": True, "fs": True},
        ),
        previous=first.observed,
    )
    assert second.refusal is None
    assert len(second.warnings) == 2
    return second.warnings


def test_b3_every_policy_warning_carries_the_documented_field_set():
    for warning in _drifted_warnings():
        assert set(warning) == POLICY_WARNING_FIELDS
        assert warning["type"] == "policy_warning"
        assert warning["authoritative"] is False
        assert warning["refused"] is False


def test_b3_subject_and_comparison_are_closed_categorical_values():
    subjects = {warning["subject"] for warning in _drifted_warnings()}
    comparisons = {warning["comparison"] for warning in _drifted_warnings()}
    assert subjects == {
        observation.SUBJECT_AGENT_SELF_REPORT,
        observation.SUBJECT_ADVERTISED_CAPABILITIES,
    }
    assert subjects <= observation.WARNING_SUBJECTS
    assert comparisons == {observation.COMPARISON_PREVIOUS_RUN_OF_SESSION}
    assert comparisons <= observation.WARNING_COMPARISONS


def test_b3_a_warning_names_which_fact_drifted_never_what_it_was():
    """No observed name, version, capability, digest, or length — ever."""
    observed_values = (
        "SomeAgent",
        "Renamed",
        "1.0.0",
        "2.0.0",
        "loadSession",
        "fs",
    )
    for warning in _drifted_warnings():
        rendered = json.dumps(warning, sort_keys=True)
        for value in observed_values:
            assert value not in rendered
        for field, carried in warning.items():
            assert isinstance(carried, (str, bool)), field
            if isinstance(carried, str):
                # Categorical vocabulary only: no free-form text can arrive here.
                assert carried in observation.WARNING_VOCABULARY, field


def test_b3_a_warning_never_gates_or_refuses():
    """Emission stays non-authoritative and non-refusing on both codes."""
    for warning in _drifted_warnings():
        assert warning["authoritative"] is False
        assert warning["refused"] is False


def test_b3_the_documented_schema_and_the_emitted_record_agree():
    """The living schema §5.5 field table is the contract; read it and check."""
    schema = (
        Path(observation.__file__).parents[3]
        / "docs"
        / "design"
        / "result-event-schema.md"
    )
    text = schema.read_text(encoding="utf-8")
    section = text.split("### 5.5")[1].split("### 5.6")[0]
    documented = {
        line.split("|")[1].strip().strip("`")
        for line in section.splitlines()
        if line.startswith("| `")
    }
    assert documented == POLICY_WARNING_FIELDS


# -- the compat profile keeps its cited deviation ----------------------------


def test_compat_profile_still_requires_its_permission_mode():
    compat = instance(CLAUDE_AGENT_ACP_COMPAT_V1)
    assert compat.profile.required_permission_mode == "default"
    assert compat.profile.permission_mode_selector_id == "mode"
    assert STANDARD_NATIVE_ACP_V1.required_permission_mode is None


# -- evidence is marked non-authoritative ------------------------------------


def test_recorded_evidence_is_marked_non_authoritative():
    verdict = observation.judge_initialize(instance(), summary())
    payload = verdict.to_evidence()
    assert payload["authoritative"] is False
    assert payload["observed"]["agent_info"] == {"name": "SomeAgent", "version": "9.9.9"}
    assert "expected" not in payload
    assert payload["refusal"] is None


def test_evidence_carries_no_expected_identity_to_compare_against():
    """There is nothing to compare a self-report *to*, by construction."""
    payload = observation.judge_initialize(instance(), summary()).to_evidence()
    rendered = str(payload)
    for banned in ("acp_agent_name", "agent_info_name", "expected", "adapter_contract"):
        assert banned not in rendered


# -- structural --------------------------------------------------------------


def test_no_identity_gate_survives_in_run_task():
    text = RUN_TASK_SOURCE.read_text(encoding="utf-8")
    for banned in (
        "AGENT_IDENTITY_MISMATCH",
        "acp_agent_name",
        "acp_agent_version",
        "initialize_attestation",
        "attest_spawn_boundary",
    ):
        assert banned not in text, f"run_task still carries {banned!r}"


def test_no_source_constant_is_compared_against_an_observation():
    """A scan, because a comparison is easy to reintroduce one call site at a time."""
    tree = ast.parse(OBSERVATION_SOURCE.read_text(encoding="utf-8"))
    compared: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for side in (node.left, *node.comparators):
            if isinstance(side, ast.Attribute):
                compared.append(side.attr)
    for banned in ("acp_agent_name", "acp_agent_version", "profile_hash", "command"):
        assert banned not in compared


def test_no_epoch_is_derived_from_an_observation():
    """A8/§13: only an operator's edit changes ``session_epoch``."""
    for source in (RUN_TASK_SOURCE, OBSERVATION_SOURCE):
        text = source.read_text(encoding="utf-8")
        assert "session_epoch + 1" not in text
        assert "session_epoch += 1" not in text
        assert "bump_epoch" not in text
        assert "derive_epoch" not in text

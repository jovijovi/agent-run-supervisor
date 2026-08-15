"""L2: what an ARS Run keeps now that the per-Run literal guard is gone.

The full-value sensitive-literal guard is removed, deliberately and with the
consequence stated: an AGENT that echoes an arbitrary environment value which
does not match a static credential pattern may have that value retained in ARS
evidence. These tests are the reverse regression for that decision — each one
fails if exact-literal matching, carry/recomposition handling, or categorical
withholding is reintroduced under any name.

What survives the removal, and is pinned here too:

* the static shape redactor (API key / Bearer / JWT / PEM) over free-form Run
  text, which is a *shape* rule and never a per-Run value set;
* the sensitive-env-**key** projection rule;
* the value-blind sealed launch material — environment values were never in
  ``spec.json``/``launch.json`` and still are not;
* every existing size ceiling and categorical failure code.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp.profile import ProfileRegistry

from .test_run_task import HAPPY_SCRIPT, Harness, _request, _run, _test_profile
from agent_run_supervisor.session import derive_session_id_for_run

SENTINEL_NAME = "ARS_ENV_SINK_SENTINEL"
ALLOWLIST = (
    "PATH",
    "HOME",
    "FAKE_AGENT_SCRIPT",
    "FAKE_AGENT_TRACE",
    SENTINEL_NAME,
)


def _harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script: dict,
    sentinel: str,
) -> Harness:
    monkeypatch.setenv(SENTINEL_NAME, sentinel)
    harness = Harness(tmp_path, monkeypatch, script)
    harness.registry = ProfileRegistry((_test_profile(base_allowlist=ALLOWLIST),))
    return harness


def _events(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text().splitlines()
    ]


# -- the removed symbols are removed, not disabled --------------------------


def test_the_guard_symbol_family_no_longer_exists() -> None:
    redaction = importlib.import_module("agent_run_supervisor.redaction")
    for name in (
        "RunTextGuard",
        "SafeText",
        "GuardCounters",
        "EMPTY_SAFE_TEXT",
        "serialized_projection_is_safe",
        "ENV_VALUE_REPLACEMENT",
        "ENV_VALUE_REPLACEMENT_BYTES",
        "GUARDED_TEXT_WITHHELD",
        "GUARDED_TEXT_WITHHELD_BYTES",
        "GUARDED_VALUE_WITHHELD",
        "GUARDED_RECORD_WITHHELD",
    ):
        assert not hasattr(redaction, name), name


def test_the_safe_logging_module_no_longer_exists() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("agent_run_supervisor.arsd.safe_logging")


def test_resolved_environment_exposes_no_sensitive_value_accessor() -> None:
    """The carrier stays ephemeral and non-serializable — with one consumer.

    Removing the guard removes the *second* consumer, so the contract narrows:
    values are handed to process spawn and to nothing else.
    """
    from agent_run_supervisor.native_acp.spec import ResolvedEnvironment

    resolved = ResolvedEnvironment((("HOME", "/home/agent", "base", 1),))
    assert not hasattr(resolved, "sensitive_values")
    assert resolved.exec_mapping == {"HOME": "/home/agent"}
    with pytest.raises(TypeError):
        resolved.__reduce__()


# -- an ordinary projected value is retained --------------------------------


def test_an_ordinary_projected_value_is_retained_in_the_final_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "ordinary-projected-value-4a91"
    script = dict(HAPPY_SCRIPT)
    script["echo_env"] = SENTINEL_NAME
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["final_message"] == f"ENV:{sentinel}"


def test_a_low_entropy_projected_value_is_retained_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``"1"`` is the value the old guard could not tell from a sequence number.

    A one-character projected value used to erase every occurrence of that
    character from Run text and to refuse any external Session id containing
    it. Nothing about it is special now.
    """
    sentinel = "1"
    script = dict(HAPPY_SCRIPT)
    script["echo_env"] = SENTINEL_NAME
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["final_message"] == "ENV:1"


def test_a_value_split_across_chunks_needs_no_carry_or_recomposition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Split chunks are simply concatenated, subject only to the byte ceiling.

    The rolling ``max_literal_chars - 1`` carry existed to catch a value split
    across two ``agent_message_chunk`` frames. With no literal set there is no
    carry: both halves are retained and the assembled message is exact.
    """
    sentinel = "split-across-two-frames-77c2"
    head, tail = sentinel[:12], sentinel[12:]
    script = dict(HAPPY_SCRIPT)
    script["final_message"] = ""
    script["final_message_chunks"] = [f"before {head}", f"{tail} after"]
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["final_message"] == f"before {sentinel} after"


def test_projected_values_are_retained_in_normalized_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "event-dynamic-field-31bd"
    script = dict(HAPPY_SCRIPT)
    script["prompt_tool_updates"] = [
        {
            "sessionUpdate": "tool_call",
            "toolCallId": f"call-{sentinel}",
            "title": sentinel,
            "kind": "read",
            "status": "pending",
        },
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": f"call-{sentinel}",
            "status": "completed",
        },
    ]
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    started = [e for e in _events(harness.run_dir()) if e.get("type") == "tool_started"]
    assert started
    assert started[0]["tool_call_id"] == f"call-{sentinel}"


def test_a_chunk_containing_a_projected_value_keeps_its_text_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``text_length`` is structural evidence again, for every chunk."""
    sentinel = "length-not-by-value-8ee0"
    script = dict(HAPPY_SCRIPT)
    script["echo_env"] = SENTINEL_NAME
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    _run(harness.task())

    deltas = [
        e for e in _events(harness.run_dir()) if e.get("type") == "agent_message_delta"
    ]
    assert deltas
    assert all("text_length" in event for event in deltas)
    assert all("text_length_withheld" not in event for event in deltas)


def test_permission_evidence_retains_the_child_tool_call_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "permission-toolcall-5c04"
    script = dict(HAPPY_SCRIPT)
    script["ask_permission"] = {
        "kind": "edit",
        "tool_call_id": f"perm-{sentinel}",
    }
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    _run(harness.task())

    mediation = [
        e
        for e in _events(harness.run_dir())
        if e.get("type") == "permission_mediation"
    ]
    assert mediation
    assert any(event.get("decision") == "deny" for event in mediation)
    assert any(event.get("tool_call_id") == f"perm-{sentinel}" for event in mediation)


def test_a_permission_option_id_equal_to_a_projected_value_is_still_selectable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The option id travels back over the wire byte-for-byte, as it must.

    An option id whose bytes happen to equal a projected environment value used
    to force a fail-closed deny. Mediation now decides on the frozen grant
    alone, so a granted read is allowed and the once-scoped option is selected.
    """
    sentinel = "wire-option-id-sentinel-5a72"
    choice = tmp_path / "selected-option.txt"
    written = tmp_path / "workspace" / "ask-target.txt"
    script = dict(HAPPY_SCRIPT)
    script["ask_permission"] = {
        "kind": "read",
        "path": str(written),
        "content": "ALLOWED_CANARY",
        "choice_path": str(choice),
        # Workspace-internal path evidence, so the subject of this test stays
        # the option id's bytes rather than read-like location containment.
        "locations": [{"path": str(written.resolve())}],
        "allow_option_ids": [sentinel],
        "options": [
            {"optionId": sentinel, "name": "Allow once", "kind": "allow_once"},
            {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
        ],
    }
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    _run(harness.task())

    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["final_message"] == "ASK_ALLOWED"
    assert choice.read_text(encoding="utf-8") == sentinel
    assert written.read_text(encoding="utf-8") == "ALLOWED_CANARY"


def test_observations_and_usage_metadata_retain_projected_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "agent-version-0d5f"
    script = dict(HAPPY_SCRIPT)
    script["agent_info"] = {"name": "fake-acp-agent", "version": sentinel}
    script["usage"] = {
        "totalTokens": 30,
        "inputTokens": 20,
        "outputTokens": 10,
        "_meta": {"vendor_note": sentinel},
    }
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    run_dir = harness.run_dir()
    effective = json.loads((run_dir / "effective.json").read_text())
    assert effective["agent_info"]["version"] == sentinel
    evidence = json.loads((run_dir / "initialize_evidence.json").read_text())
    assert evidence["authoritative"] is False
    assert evidence["observed"]["agent_info"]["version"] == sentinel
    payload = json.loads((run_dir / "result.json").read_text())
    assert payload["usage"]["_meta"]["vendor_note"] == sentinel
    record = harness.session_store().open_session("sess-native-1")
    assert record.native_last_agent_info_version == sentinel


def test_stderr_retains_an_ordinary_projected_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "stderr-text-2f6c"
    script = dict(HAPPY_SCRIPT)
    script["stderr_text"] = f"agent warning: {sentinel}\n"
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    assert sentinel in (harness.run_dir() / "stderr.log").read_text()


def test_an_external_session_id_equal_to_a_projected_value_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The categorical collision refusal is gone, type and code included.

    The id is bound, persisted, prompted against, and returned — the ordinary
    path, with no sensitive-literal comparison anywhere before it.
    """
    from agent_run_supervisor.native_acp import driver as driver_module

    assert not hasattr(driver_module, "SESSION_EXTERNAL_ID_SENSITIVE_COLLISION")
    assert not hasattr(driver_module, "SessionExternalIdSensitiveCollision")

    sentinel = "external-session-id-9a20"
    script = dict(HAPPY_SCRIPT)
    script["session_id"] = sentinel
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(
        harness.task(request=_request(session_id=None))
    )

    assert result.status is AgentRunStatus.COMPLETED
    assert (harness.run_dir() / "prompt-dispatch-started").exists()
    record = harness.session_store().open_session(derive_session_id_for_run("run-0001"))
    assert record.agent_session_id == sentinel


# -- what did not change ----------------------------------------------------


def test_static_shape_redaction_still_applies_to_free_form_run_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shape rules are not the removed guard; they never had a value set.

    The API-key/Bearer/JWT/PEM patterns keep clearing credential-shaped text
    out of the final message and stderr, and the redaction report keeps naming
    which pattern fired.
    """
    from agent_run_supervisor.redaction import REDACTED_INLINE

    sentinel = "static-pattern-probe-1f30"
    # Assembled at runtime so the repository's own secret scanner does not see
    # a credential-shaped literal in this file.
    secret = "sk-" + "A" * 32
    pem_header = "-----BEGIN " + "RSA PRIVATE KEY" + "-----"
    script = dict(HAPPY_SCRIPT)
    script["final_message"] = f"leaked {secret} tail"
    script["stderr_text"] = f"Authorization: Bearer abc.def.ghi\n{pem_header}\n"
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    run_dir = harness.run_dir()
    payload = json.loads((run_dir / "result.json").read_text())
    assert payload["final_message"] == f"leaked {REDACTED_INLINE} tail"
    stderr_log = (run_dir / "stderr.log").read_text()
    assert "Bearer abc.def.ghi" not in stderr_log
    assert "BEGIN RSA PRIVATE KEY" not in stderr_log
    report = json.loads((run_dir / "redaction-report.json").read_text())
    fired = {match["pattern"] for match in report["matches"]}
    assert "openai_api_key" in fired
    assert {"bearer_token", "pem_private_key"} <= fired


def test_sensitive_env_key_projections_are_still_redacted() -> None:
    from agent_run_supervisor.redaction import (
        REDACTED_PLACEHOLDER,
        redact_env,
        redact_mapping,
    )

    redacted, report = redact_env(
        {"OPENAI_API_KEY": "value-a", "SOME_TOKEN": "value-b", "TERM": "xterm"}
    )
    assert redacted["OPENAI_API_KEY"] == REDACTED_PLACEHOLDER
    assert redacted["SOME_TOKEN"] == REDACTED_PLACEHOLDER
    assert redacted["TERM"] == "xterm"
    assert {match.pattern_name for match in report.matches} == {"env_sensitive_key"}

    mapped, _ = redact_mapping({"env": {"ANTHROPIC_AUTH": "value-c", "LANG": "C"}})
    assert mapped["env"]["ANTHROPIC_AUTH"] == REDACTED_PLACEHOLDER
    assert mapped["env"]["LANG"] == "C"


def test_environment_values_stay_out_of_spec_and_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sealed material was value-blind before the guard and still is.

    Removing the per-Run literal guard removes a *dynamic text* boundary. The
    structural one is untouched: the launch snapshot records the name, its
    source class, its precedence layer, and its redaction status, and nothing
    that could reconstruct the value.
    """
    sentinel = "sealed-material-probe-6b17"
    script = dict(HAPPY_SCRIPT)
    script["echo_env"] = SENTINEL_NAME
    harness = _harness(tmp_path, monkeypatch, script, sentinel)

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    run_dir = harness.run_dir()
    spec_raw = (run_dir / "spec.json").read_text()
    launch_raw = (run_dir / "launch.json").read_text()
    assert sentinel not in spec_raw
    assert sentinel not in launch_raw
    assert os.fsencode(sentinel) not in (run_dir / "launch.json").read_bytes()
    launch = json.loads(launch_raw)
    projected = {item["name"] for item in launch["env"]["names"]}
    assert SENTINEL_NAME in projected
    assert launch["env"]["values_persisted"] is False
    for item in launch["env"]["names"]:
        assert set(item) == {"name", "source", "precedence", "redacted"}

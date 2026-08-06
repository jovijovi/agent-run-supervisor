"""WP3.11 — the operator surface: ``agents validate``, ``agents doctor``, ``run inspect``.

Three commands, and deliberately no fourth. There is no ``promote``, no
``rollback``, and no ``--force``: nothing here installs an artifact, edits a
service unit, restarts the daemon, escalates privilege, or contacts a provider.

``agents validate`` applies the **identical** check the daemon applies at
startup, so what an operator sees offline is what the daemon will decide at its
next start. It prints entry ids, counts, environment **names**, source classes,
and rule outcomes — never a normalized overlay or mediation value.

``run inspect`` classifies the record's schema **before** it selects a verifier.
For a reset-schema record it recomputes the value-blind launch hash. For a
pre-reset record it withholds environment fields, raw documents, seal material,
and free-form text, and never recomputes a hash over value-bearing material —
because doing so would make the value an input to a digest ARS then publishes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from pathlib import Path

import pytest

from agent_run_supervisor import cli, commands
from agent_run_supervisor.native_acp import profile as profile_mod
from agent_run_supervisor.native_acp import spec
from agent_run_supervisor.native_acp.agent_registration import AgentEntry

from tests.native_acp import registry_fixtures as fx

SENTINEL = "SeNtInEl-legacy-env-value"
PROBE_SENTINEL = "PrObE-SeNtInEl-projected-value-7a3f"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self) -> str:
        formatter = logging.Formatter("%(name)s|%(message)s")
        return "\n".join(formatter.format(record) for record in self.records)


def _probe_instance():
    return profile_mod.AgentInstance(
        profile_mod.STANDARD_NATIVE_ACP_V1,
        AgentEntry(
            agent_id="a-1",
            profile_id="standard-native-acp-v1",
            command="some-agent",
            env_overlay=(("AGENT_TOKEN", PROBE_SENTINEL),),
        ),
    )


def _probe_env():
    return spec.resolve_run_environment(
        arsd_env={"PATH": "/usr/bin"},
        profile=profile_mod.STANDARD_NATIVE_ACP_V1,
        entry=_probe_instance().entry,
    )


def run_cli(argv, capsys):
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else None)


# -- the command surface -----------------------------------------------------


def test_the_runtime_binding_group_is_gone():
    parser = cli._build_parser()
    commands_available = {
        name
        for action in parser._actions
        if hasattr(action, "choices") and action.choices
        for name in action.choices
    }
    assert "runtime-binding" not in commands_available
    assert "agents" in commands_available
    assert not hasattr(commands, "cmd_runtime_binding")


def test_there_is_no_promote_rollback_or_force_subcommand():
    """The prose may name what is refused; no parser may offer it."""
    parser = cli._build_parser()
    offered = {
        name
        for action in parser._actions
        if hasattr(action, "choices") and action.choices
        for name in action.choices
    }
    for subparser in (
        action
        for action in parser._actions
        if hasattr(action, "choices") and action.choices
    ):
        for child in subparser.choices.values():
            for action in child._actions:
                offered.update(getattr(action, "choices", None) or ())
                offered.update(action.option_strings)
    for banned in ("promote", "rollback", "--force", "--trusted-uid", "--service-uid"):
        assert banned not in offered


def test_agents_requires_a_subcommand(capsys):
    assert cli.main(["agents"]) == 2


def test_run_requires_the_inspect_subcommand(capsys):
    """``run`` is a parent, not a command. The exec leaf it used to hold is gone."""
    assert cli.main(["run"]) == 2
    assert "inspect" in capsys.readouterr().err


# -- agents validate ---------------------------------------------------------


def test_validate_accepts_a_well_formed_registry(tmp_path, capsys):
    path = fx.write_registry(
        tmp_path, entries={"a-1": fx.full_entry(), "b-2": fx.minimal_entry()}
    )
    code, report = run_cli(["agents", "validate", "--agents-file", str(path)], capsys)
    assert code == 0
    assert report["valid"] is True
    assert report["agent_count"] == 2
    assert report["agents"][0]["agent_id"] == "a-1"


def test_validate_prints_environment_names_and_never_values(tmp_path, capsys):
    entry = fx.full_entry(env_overlay={"SOME_AGENT_HOME": SENTINEL})
    path = fx.write_registry(tmp_path, entries={"a-1": entry})
    code, report = run_cli(["agents", "validate", "--agents-file", str(path)], capsys)
    assert code == 0
    rendered = json.dumps(report, sort_keys=True)
    assert SENTINEL not in rendered
    assert "SOME_AGENT_HOME" in rendered
    assert report["agents"][0]["env_overlay_names"] == ["SOME_AGENT_HOME"]
    assert "env_overlay" not in report["agents"][0]


def test_validate_never_prints_a_mediation_value(tmp_path, capsys):
    path = fx.write_registry(
        tmp_path, entries={"a-1": fx.minimal_entry(mediation=fx.MEDIATION_ID)}
    )
    _, report = run_cli(["agents", "validate", "--agents-file", str(path)], capsys)
    rendered = json.dumps(report, sort_keys=True)
    for _, value in profile_mod.MEDIATION_BINDINGS[fx.MEDIATION_ID]:
        assert value not in rendered
    assert report["agents"][0]["mediation"] == fx.MEDIATION_ID


def test_validate_applies_the_identical_startup_collision_check(tmp_path, capsys):
    reserved = sorted(profile_mod.RESERVED_MEDIATION_KEYS)[0]
    path = fx.write_registry(
        tmp_path, entries={"a-1": fx.minimal_entry(env_overlay={reserved: "allow"})}
    )
    code, report = run_cli(["agents", "validate", "--agents-file", str(path)], capsys)
    assert code == 1
    assert report["valid"] is False
    assert report["rule"] == "MEDIATION_KEY_COLLISION"
    assert "allow" not in json.dumps(report, sort_keys=True)


def test_validate_reports_the_rule_for_every_refusal_class(tmp_path, capsys):
    cases = {
        "REGISTRY_SCHEMA_VERSION": fx.registry_text(schema_version=9),
        "REGISTRY_UNKNOWN_KEY": fx.registry_text(extra_lines=['extra = "x"']),
        "ENTRY_COMMAND_INVALID": fx.registry_text(
            entries={"a-1": fx.minimal_entry(command="rel/path")}
        ),
        "ENTRY_UNKNOWN_PROFILE": fx.registry_text(
            entries={"a-1": fx.minimal_entry(profile="nope")}
        ),
    }
    for expected, text in cases.items():
        path = fx.write_registry(tmp_path, name=f"{expected}.toml", text=text)
        code, report = run_cli(
            ["agents", "validate", "--agents-file", str(path)], capsys
        )
        assert code == 1
        assert report["rule"] == expected


def test_validate_never_writes_anything(tmp_path, capsys):
    path = fx.write_registry(tmp_path)
    before = {item.name for item in tmp_path.iterdir()}
    run_cli(["agents", "validate", "--agents-file", str(path)], capsys)
    assert {item.name for item in tmp_path.iterdir()} == before


# -- agents doctor -----------------------------------------------------------


def test_doctor_reports_the_projected_environment_name_set(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SOME_AGENT_CONFIG", "present")
    path = fx.write_registry(
        tmp_path,
        entries={
            "a-1": fx.minimal_entry(
                env_passthrough=["SOME_AGENT_CONFIG", "DEFINITELY_ABSENT_NAME"],
                env_overlay={"SOME_AGENT_HOME": SENTINEL},
            )
        },
    )
    code, report = run_cli(
        ["agents", "doctor", "--agents-file", str(path), "--no-probe"], capsys
    )
    assert code == 0
    rendered = json.dumps(report, sort_keys=True)
    assert SENTINEL not in rendered
    agent = report["agents"][0]
    names = {item["name"] for item in agent["env"]["names"]}
    assert "PATH" in names or "HOME" in names
    assert "SOME_AGENT_CONFIG" in names
    assert agent["env"]["declared_absent"] == ["DEFINITELY_ABSENT_NAME"]
    assert agent["env"]["values_persisted"] is False


def test_doctor_can_target_one_agent(tmp_path, capsys):
    path = fx.write_registry(
        tmp_path, entries={"a-1": fx.minimal_entry(), "b-2": fx.minimal_entry()}
    )
    _, report = run_cli(
        ["agents", "doctor", "--agents-file", str(path), "--agent", "b-2", "--no-probe"],
        capsys,
    )
    assert [agent["agent_id"] for agent in report["agents"]] == ["b-2"]


def test_doctor_refuses_an_unregistered_agent(tmp_path, capsys):
    path = fx.write_registry(tmp_path)
    code, report = run_cli(
        ["agents", "doctor", "--agents-file", str(path), "--agent", "nope", "--no-probe"],
        capsys,
    )
    assert code == 1
    assert report["rule"] == "AGENT_NOT_REGISTERED"


# -- agents doctor: the default path really starts the agent ------------------
#
# Driven against the repository's local fake ACP agent. No real agent, no
# provider, no model call: the fake answers ``initialize`` and nothing else is
# ever asked of it.

FAKE_AGENT_PATH = (
    Path(__file__).resolve().parent / "native_acp" / "fake_agent.py"
)


def probe_registry(tmp_path, *, script=None, **entry_overrides):
    """A registry whose one command is the local fake ACP agent."""
    conf = tmp_path / "conf"
    conf.mkdir(exist_ok=True)
    entry = fx.minimal_entry(
        command=sys.executable,
        args=[str(FAKE_AGENT_PATH)],
        env_passthrough=["FAKE_AGENT_SCRIPT", "FAKE_AGENT_TRACE"],
    )
    entry.update(entry_overrides)
    return fx.write_registry(conf, entries={"probe-agent": entry})


def test_doctor_default_path_performs_one_zero_prompt_initialize(
    tmp_path, capsys, monkeypatch
):
    """WP3.11: the default command starts the agent and speaks exactly once."""
    pytest.importorskip("acp")
    trace = tmp_path / "trace.log"
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps({"final_message": "unused"}))
    monkeypatch.setenv("FAKE_AGENT_TRACE", str(trace))
    path = probe_registry(tmp_path)

    code, report = run_cli(["agents", "doctor", "--agents-file", str(path)], capsys)

    assert code == 0
    agent = report["agents"][0]
    assert agent["probe"]["outcome"] == "ok"
    assert agent["probe"]["protocol_version"] == 1
    assert agent["probe"]["load_session_advertised"] is True

    methods = [line for line in trace.read_text().splitlines() if line]
    assert methods == ["initialize"], methods


def test_doctor_default_path_never_opens_or_prompts_a_session(
    tmp_path, capsys, monkeypatch
):
    """Zero-prompt means zero: no session is created, loaded, or prompted."""
    pytest.importorskip("acp")
    trace = tmp_path / "trace.log"
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps({"final_message": "unused"}))
    monkeypatch.setenv("FAKE_AGENT_TRACE", str(trace))
    path = probe_registry(tmp_path)

    run_cli(["agents", "doctor", "--agents-file", str(path)], capsys)

    methods = trace.read_text()
    for banned in ("session/new", "session/load", "session/prompt", "session/cancel"):
        assert banned not in methods


def test_doctor_default_path_reaps_the_child_on_success(tmp_path, capsys, monkeypatch):
    """A diagnostic that leaves an agent running is a leak, not a diagnostic."""
    pytest.importorskip("acp")
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps({"final_message": "unused"}))
    monkeypatch.setenv("FAKE_AGENT_TRACE", str(tmp_path / "trace.log"))
    path = probe_registry(tmp_path)

    spawned: list[int] = []
    real_spawn = commands_probe_spawn()

    async def recording(**kwargs):
        proc = await real_spawn(**kwargs)
        spawned.append(proc.pid)
        return proc

    monkeypatch.setattr(commands, "spawn_managed_process", recording, raising=False)
    code, _ = run_cli(["agents", "doctor", "--agents-file", str(path)], capsys)

    assert code == 0
    assert len(spawned) == 1
    assert not _pid_alive(spawned[0])


def test_doctor_default_path_reaps_the_child_when_the_probe_fails(
    tmp_path, capsys, monkeypatch
):
    """The failure path cleans up on exactly the same terms as the happy one."""
    pytest.importorskip("acp")
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps({"final_message": "unused"}))
    monkeypatch.setenv("FAKE_AGENT_TRACE", str(tmp_path / "trace.log"))
    # Starts, then exits without ever speaking ACP: the child really existed,
    # so the reap is a real reap rather than a vacuous one.
    path = probe_registry(tmp_path, command="/bin/true", args=[])

    spawned: list[int] = []
    real_spawn = commands_probe_spawn()

    async def recording(**kwargs):
        proc = await real_spawn(**kwargs)
        spawned.append(proc.pid)
        return proc

    monkeypatch.setattr(commands, "spawn_managed_process", recording, raising=False)
    code, report = run_cli(["agents", "doctor", "--agents-file", str(path)], capsys)

    assert code == 1
    assert report["agents"][0]["probe"]["outcome"] == "failed"
    assert len(spawned) == 1
    assert not _pid_alive(spawned[0])


def test_doctor_probe_failure_is_a_stable_categorical_code(tmp_path, capsys, monkeypatch):
    """No exception text, no stderr, no child free text — a code and nothing else."""
    pytest.importorskip("acp")
    monkeypatch.setenv(
        "FAKE_AGENT_SCRIPT", json.dumps({"stderr_text": f"boom {SENTINEL}"})
    )
    monkeypatch.setenv("FAKE_AGENT_TRACE", str(tmp_path / "trace.log"))
    # A command that is not there at all: classified from the errno ARS saw.
    path = probe_registry(tmp_path, command="/nonexistent/no-such-agent", args=[])

    code, report = run_cli(["agents", "doctor", "--agents-file", str(path)], capsys)

    assert code == 1
    probe = report["agents"][0]["probe"]
    assert probe["outcome"] == "failed"
    assert probe["code"] == "COMMAND_NOT_FOUND"
    # The whole report carries no child text and no environment value. The
    # operator's own declared command *is* echoed, deliberately and elsewhere:
    # it is what they typed, not something the child said.
    rendered = json.dumps(report, sort_keys=True)
    assert SENTINEL not in rendered
    for banned in ("Traceback", "Errno", "stderr", "Exception"):
        assert banned not in rendered
    assert "no-such-agent" not in json.dumps(probe, sort_keys=True)


def test_doctor_probe_reports_only_allowlisted_value_blind_facts(
    tmp_path, capsys, monkeypatch
):
    """The agent's self-report is child-controlled free text and never escapes."""
    pytest.importorskip("acp")
    monkeypatch.setenv(
        "FAKE_AGENT_SCRIPT",
        json.dumps({"agent_info": {"name": SENTINEL, "version": SENTINEL}}),
    )
    monkeypatch.setenv("FAKE_AGENT_TRACE", str(tmp_path / "trace.log"))
    path = probe_registry(tmp_path)

    code, report = run_cli(["agents", "doctor", "--agents-file", str(path)], capsys)

    assert code == 0
    probe = report["agents"][0]["probe"]
    assert set(probe) == {
        "outcome",
        "code",
        "protocol_version",
        "load_session_advertised",
        "advertised_capability_count",
        "required_capabilities_present",
        "forbidden_capabilities_present",
    }
    assert SENTINEL not in json.dumps(report, sort_keys=True)
    assert "agent_info" not in probe


def test_doctor_probe_reports_a_contract_refusal_categorically(
    tmp_path, capsys, monkeypatch
):
    """One of the five observation refusals, named — not an exception."""
    pytest.importorskip("acp")
    monkeypatch.setenv("FAKE_AGENT_SCRIPT", json.dumps({"protocol_version": 99}))
    monkeypatch.setenv("FAKE_AGENT_TRACE", str(tmp_path / "trace.log"))
    path = probe_registry(tmp_path)

    code, report = run_cli(["agents", "doctor", "--agents-file", str(path)], capsys)

    assert code == 1
    probe = report["agents"][0]["probe"]
    assert probe["outcome"] == "refused"
    assert probe["code"] == "PROTOCOL_MISMATCH"


def test_doctor_no_probe_starts_nothing_and_stays_value_blind(
    tmp_path, capsys, monkeypatch
):
    """``--no-probe`` remains the explicit no-child path."""
    path = probe_registry(tmp_path)

    def refuse(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("--no-probe must not start a child")

    monkeypatch.setattr(commands, "spawn_managed_process", refuse, raising=False)
    code, report = run_cli(
        ["agents", "doctor", "--agents-file", str(path), "--no-probe"], capsys
    )

    assert code == 0
    assert report["agents"][0]["probe"] is None
    assert report["agents"][0]["env"]["values_persisted"] is False


def commands_probe_spawn():
    from agent_run_supervisor.managed_process import spawn_managed_process

    return spawn_managed_process


def _pid_alive(pid: int) -> bool:
    import errno as _errno

    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno != _errno.ESRCH
    return True


def test_doctor_reports_the_declared_command_and_argv(tmp_path, capsys):
    path = fx.write_registry(
        tmp_path, entries={"a-1": fx.minimal_entry(command="some-agent", args=["acp"])}
    )
    _, report = run_cli(
        ["agents", "doctor", "--agents-file", str(path), "--no-probe"], capsys
    )
    agent = report["agents"][0]
    assert agent["command"] == "some-agent"
    assert agent["argv"] == ["some-agent", "acp"]


# -- run inspect: the reset schema -------------------------------------------


def reset_launch_record(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    resolved = spec.resolve_run_environment(
        arsd_env={"HOME": "/home/svc", "TOKEN_NAME": SENTINEL},
        profile=profile_mod.STANDARD_NATIVE_ACP_V1,
        entry=AgentEntry(
            agent_id="a-1",
            profile_id="standard-native-acp-v1",
            command="some-agent",
            env_passthrough=("TOKEN_NAME",),
        ),
    )
    snapshot = spec.LaunchSnapshot(
        command="some-agent",
        argv=("some-agent",),
        profile_id="standard-native-acp-v1",
        profile_revision=1,
        profile_hash="0" * 64,
        agent_id="a-1",
        env=resolved.value_blind_projection(),
    )
    payload = snapshot.to_dict()
    payload["launch_spec_hash"] = snapshot.launch_hash()
    (run_dir / "launch.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return run_dir


def test_inspect_verifies_a_reset_schema_record(tmp_path, capsys):
    run_dir = reset_launch_record(tmp_path)
    code, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    assert code == 0
    assert report["schema"] == "reset"
    assert report["legacy_value_bearing"] is False
    assert report["seal_verified"] is True
    assert report["agent_id"] == "a-1"
    assert report["command"] == "some-agent"
    assert SENTINEL not in json.dumps(report, sort_keys=True)


def test_inspect_reports_the_value_blind_environment(tmp_path, capsys):
    run_dir = reset_launch_record(tmp_path)
    _, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    names = {item["name"] for item in report["env"]["names"]}
    assert "TOKEN_NAME" in names
    assert report["env"]["values_persisted"] is False
    assert report["env"]["redaction"] == "all-values-withheld"


def test_inspect_detects_a_tampered_reset_seal(tmp_path, capsys):
    run_dir = reset_launch_record(tmp_path)
    path = run_dir / "launch.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["command"] = "another-agent"
    payload["argv"] = ["another-agent"]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    code, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    assert code == 1
    assert report["seal_verified"] is False


# -- run inspect: the legacy, value-bearing schema ---------------------------


def legacy_launch_record(tmp_path: Path) -> Path:
    """A pre-reset record: value-bearing env, a seal over it, free-form text."""
    run_dir = tmp_path / "legacy-run"
    run_dir.mkdir()
    payload = {
        "executable": "/opt/agent-run-supervisor/artifacts/node/bin/node",
        "argv": ["/opt/.../node", "--no-global-search-paths", "/opt/.../index.js"],
        "env_allowlist": ["HOME", "PATH"],
        "credential_refs": ["codex-home-auth"],
        "profile_id": "codex-acp-1.1.7",
        "profile_revision": 3,
        "profile_hash": "a" * 64,
        "config_schema_hash": "b" * 64,
        "permission_env": [["OPENCODE_PERMISSION", SENTINEL]],
        "transport": "stdio",
        "fixed_env": [["CODEX_HOME", "/home/svc/.codex"], ["TOKEN_NAME", SENTINEL]],
        "expected_runtime": {"cli": {"sha256": "c" * 64}, "node_path": "/opt/.../node"},
        "runtime_provenance": {"generation_id": "gen-1", "manifest_sha256": "d" * 64},
        "launch_spec_hash": "e" * 64,
        "notes": f"free-form text mentioning {SENTINEL}",
    }
    (run_dir / "launch.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return run_dir


def test_inspect_classifies_the_schema_before_selecting_a_verifier(tmp_path, capsys):
    run_dir = legacy_launch_record(tmp_path)
    code, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    assert code == 0
    assert report["schema"] == "legacy"
    assert report["legacy_value_bearing"] is True
    assert report["environment_values_withheld"] is True
    assert report["launch_seal_verification"] == "not_performed_value_bearing_legacy"


def test_inspect_never_recomputes_a_hash_over_value_bearing_material(
    tmp_path, capsys, monkeypatch
):
    """The proof is a hash function that raises: the legacy branch never calls it."""
    run_dir = legacy_launch_record(tmp_path)

    def refuse(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("a hash was recomputed over value-bearing legacy material")

    monkeypatch.setattr(commands, "_recompute_launch_hash", refuse)
    code, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    assert code == 0
    assert report["schema"] == "legacy"


def test_inspect_withholds_every_legacy_value_bearing_field(tmp_path, capsys):
    run_dir = legacy_launch_record(tmp_path)
    _, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    rendered = json.dumps(report, sort_keys=True)
    assert SENTINEL not in rendered
    assert "/home/svc/.codex" not in rendered
    for banned in ("fixed_env", "permission_env", "env_allowlist", "notes"):
        assert banned not in report


def test_inspect_withholds_legacy_seal_material_and_raw_documents(tmp_path, capsys):
    run_dir = legacy_launch_record(tmp_path)
    _, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    rendered = json.dumps(report, sort_keys=True)
    assert "e" * 64 not in rendered
    assert "c" * 64 not in rendered
    assert "d" * 64 not in rendered
    for banned in ("expected_runtime", "runtime_provenance", "raw", "document"):
        assert banned not in report


def test_inspect_withholds_legacy_free_form_text_categorically(tmp_path, capsys):
    run_dir = legacy_launch_record(tmp_path)
    _, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    assert report["text_evidence"] == "LEGACY_TEXT_EVIDENCE_WITHHELD"


@pytest.mark.parametrize(
    "extra",
    [
        {"fixed_env": [["TOKEN_NAME", SENTINEL]]},
        {"permission_env": [["OPENCODE_PERMISSION", SENTINEL]]},
        {"env_allowlist": ["HOME", "PATH"]},
        {"expected_runtime": {"cli": {"sha256": "c" * 64}}},
        {"runtime_provenance": {"generation_id": "gen-1"}},
        {"notes": f"free-form text mentioning {SENTINEL}"},
    ],
)
def test_a_hybrid_record_is_classified_legacy_and_never_hashed(
    tmp_path, capsys, monkeypatch, extra
):
    """A record is reset-schema only if it is *exactly* the reset projection.

    A hybrid — current ``schema_version`` and a value-blind ``env`` block, plus
    one surviving value-bearing key — is the dangerous shape: it looks new
    enough to pass a loose check, and then its value-bearing key becomes input
    to a digest ARS publishes. Classification therefore requires the exact
    production shape, so anything else lands on the withholding path.
    """
    run_dir = reset_launch_record(tmp_path)
    path = run_dir / "launch.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(extra)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def refuse(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("a hash was recomputed over a hybrid launch record")

    monkeypatch.setattr(commands, "_recompute_launch_hash", refuse)
    code, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)

    assert code == 0
    assert report["schema"] == "legacy"
    assert report["legacy_value_bearing"] is True
    assert report["launch_seal_verification"] == "not_performed_value_bearing_legacy"
    rendered = json.dumps(report, sort_keys=True)
    assert SENTINEL not in rendered
    for banned in ("recomputed_launch_spec_hash", "embedded_seal", "seal_verified", "env"):
        assert banned not in report


def test_a_hybrid_record_leaks_no_seal_material_from_the_reset_half(tmp_path, capsys):
    """The reset half of a hybrid is not evidence either: it is withheld whole."""
    run_dir = reset_launch_record(tmp_path)
    path = run_dir / "launch.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    genuine_seal = payload["launch_spec_hash"]
    payload["fixed_env"] = [["TOKEN_NAME", SENTINEL]]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    _, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    rendered = json.dumps(report, sort_keys=True)
    assert genuine_seal not in rendered
    assert SENTINEL not in rendered
    assert report["text_evidence"] == "LEGACY_TEXT_EVIDENCE_WITHHELD"


def test_a_record_missing_a_required_reset_key_is_not_reset(tmp_path, capsys):
    """Subtraction is as disqualifying as addition: exact means exact."""
    run_dir = reset_launch_record(tmp_path)
    path = run_dir / "launch.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["mediation_id"]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    code, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    assert code == 0
    assert report["schema"] == "legacy"


def test_a_reset_record_with_a_malformed_env_block_is_not_reset(tmp_path, capsys):
    """A value-bearing ``env`` block must not ride in on the reset key name."""
    run_dir = reset_launch_record(tmp_path)
    path = run_dir / "launch.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["env"] = {"values": {"TOKEN_NAME": SENTINEL}}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    code, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    assert code == 0
    assert report["schema"] == "legacy"
    assert SENTINEL not in json.dumps(report, sort_keys=True)


def test_inspect_reports_a_missing_or_malformed_record(tmp_path, capsys):
    missing = tmp_path / "nothing"
    missing.mkdir()
    code, report = run_cli(["run", "inspect", "--run-dir", str(missing)], capsys)
    assert code == 1
    assert report["error"] == "LAUNCH_RECORD_MISSING"

    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "launch.json").write_text("[]", encoding="utf-8")
    code, report = run_cli(["run", "inspect", "--run-dir", str(malformed)], capsys)
    assert code == 1
    assert report["error"] == "LAUNCH_RECORD_MALFORMED"


def test_inspect_never_writes_anything(tmp_path, capsys):
    run_dir = reset_launch_record(tmp_path)
    before = {item.name for item in run_dir.iterdir()}
    run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    assert {item.name for item in run_dir.iterdir()} == before


# -- B: the probe is bounded on every path ------------------------------------
#
# ``agents doctor`` is the one diagnostic that starts an external child. Two
# things therefore have to hold on *every* path, not the happy one: the child is
# never leaked, and cleanup is bounded at every step — SDK import, client
# creation, spawn, open, initialize, close, TERM/KILL/reap, report.


class _FakeProc:
    """A process group that answers exactly as scripted, and records the calls.

    Two independent facts, because they really are independent: which signal
    reaps the **leader** (``exits_on``) and which one empties the **group**
    (``group_clears_on``). A descendant that inherited the group outlives its
    parent, so a fake that conflated them could not express the case that
    matters.
    """

    def __init__(
        self, *, exits_on: str | None = "term", group_clears_on: str | None = "same"
    ) -> None:
        self.exits_on = exits_on
        self.group_clears_on = exits_on if group_clears_on == "same" else group_clears_on
        self.calls: list[str] = []
        self.reaped = False

    def terminate_group(self, *, reason: str = "") -> None:
        self.calls.append("term")

    def kill_group(self, *, reason: str = "") -> None:
        self.calls.append("kill")

    def group_is_gone(self) -> bool:
        # Deliberately not recorded in ``calls``: that log is the signal/wait
        # sequence, and a liveness probe is neither.
        return self.group_clears_on is not None and self.group_clears_on in self.calls

    async def wait(self):
        self.calls.append("wait")
        if self.reaped:
            return "exited"
        if self.exits_on is not None and self.exits_on in self.calls:
            self.reaped = True
            return "exited"
        await asyncio.sleep(3600)


class _FakeDriver:
    def __init__(self, *, close_behavior: str = "clean") -> None:
        self.close_behavior = close_behavior
        self.closed = False

    async def close(self) -> None:
        if self.close_behavior == "raise":
            raise RuntimeError("close failed")
        if self.close_behavior == "hang":
            await asyncio.sleep(3600)
        self.closed = True


@pytest.fixture()
def quick_cleanup(monkeypatch):
    """Shrink the cleanup bounds so the matrix stays deterministic and fast."""
    monkeypatch.setattr(commands, "PROBE_CLOSE_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(commands, "PROBE_TERM_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(commands, "PROBE_KILL_GRACE_SECONDS", 0.2)


def _cleanup(driver, proc):
    return asyncio.run(commands._probe_cleanup(driver, proc))


def test_b3_a_child_that_exits_on_term_is_reaped_without_escalation(quick_cleanup):
    proc = _FakeProc(exits_on="term")
    assert _cleanup(_FakeDriver(), proc) is None
    assert proc.calls == ["term", "wait"]
    assert proc.reaped is True


def test_b3_a_term_ignoring_child_is_escalated_to_sigkill_and_reaped(quick_cleanup):
    proc = _FakeProc(exits_on="kill")
    assert _cleanup(_FakeDriver(), proc) is None
    assert proc.calls == ["term", "wait", "kill", "wait"]
    assert proc.reaped is True


def test_b3_a_group_that_survives_sigkill_is_reported_not_suppressed(quick_cleanup):
    """Never return with the group alive and the outcome unchanged."""
    proc = _FakeProc(exits_on=None)
    assert _cleanup(_FakeDriver(), proc) == commands.PROBE_CLEANUP_FAILED
    assert proc.calls == ["term", "wait", "kill", "wait"]


@pytest.mark.parametrize("behavior", ["hang", "raise"])
def test_b3_a_failing_or_hanging_close_still_reaps_the_group(behavior, quick_cleanup):
    proc = _FakeProc(exits_on="term")
    assert _cleanup(_FakeDriver(close_behavior=behavior), proc) is None
    assert proc.calls == ["term", "wait"]
    assert proc.reaped is True


def test_b3_cleanup_without_a_driver_still_reaps_the_group(quick_cleanup):
    """Setup can fail after the spawn; the group is still ours to reap."""
    proc = _FakeProc(exits_on="term")
    assert _cleanup(None, proc) is None
    assert proc.reaped is True


# -- B2: leader reap is not group absence -------------------------------------


def test_b2_a_leader_reaped_on_term_with_a_surviving_group_escalates(quick_cleanup):
    """The leader exits on SIGTERM; a descendant in its group does not."""
    proc = _FakeProc(exits_on="term", group_clears_on="kill")
    assert _cleanup(None, proc) is None
    assert proc.calls == ["term", "wait", "kill", "wait"]


def test_b2_a_reaped_leader_never_certifies_a_surviving_group(quick_cleanup):
    """Reaping the leader is not evidence about anything else in the group."""
    proc = _FakeProc(exits_on="term", group_clears_on=None)
    assert _cleanup(None, proc) == commands.PROBE_CLEANUP_FAILED
    assert proc.reaped is True


# A real leader that leaves a descendant behind in its own process group, then
# exits. Two details make this the sharp case rather than a toy:
#
#   * SIGTERM is set to SIG_IGN **before** the fork/exec. POSIX preserves an
#     ignored disposition across ``execve``, so the descendant ignores SIGTERM
#     from its first instruction — there is no window where its own startup
#     races the signal.
#   * its stdio goes to /dev/null, so it does not hold the inherited pipes open.
#     stderr therefore reaches EOF the moment the leader exits, and
#     ``ManagedProcess.wait()`` returns promptly and looks entirely clean.
LEADER_WITH_ORPHAN = (
    "import os, signal, subprocess, sys\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "devnull = os.open(os.devnull, os.O_RDWR)\n"
    "child = subprocess.Popen(\n"
    "    [sys.executable, '-c', 'import time; time.sleep(600)'],\n"
    "    stdin=devnull, stdout=devnull, stderr=devnull)\n"
    "sys.stdout.write(str(child.pid) + '\\n')\n"
    "sys.stdout.flush()\n"
    "os._exit(0)\n"
)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_b2_a_real_surviving_group_descendant_is_never_reported_clean(
    tmp_path, monkeypatch
):
    """B2 on a real POSIX group, not a fake.

    ``ManagedProcess.wait()`` proves the direct leader was reaped. It proves
    nothing about the process *group* the probe launched: a descendant that
    inherited the group, ignored SIGTERM, and closed its inherited stderr
    outlives the leader silently, and cleanup that certifies on leader reap
    alone hands the operator a clean report over a live agent.
    """
    from agent_run_supervisor.managed_process import (
        ManagedProcessLimits,
        spawn_managed_process,
    )

    monkeypatch.setattr(commands, "PROBE_TERM_GRACE_SECONDS", 1.5)
    monkeypatch.setattr(commands, "PROBE_KILL_GRACE_SECONDS", 1.5)

    async def case():
        proc = await spawn_managed_process(
            argv=[sys.executable, "-u", "-c", LEADER_WITH_ORPHAN],
            cwd=tmp_path,
            env=dict(os.environ),
            limits=ManagedProcessLimits(),
        )
        descendant = int((await asyncio.wait_for(proc.stdout.readline(), 10)).strip())
        try:
            # Preconditions: one group, the descendant is really in it, and it
            # really does survive the SIGTERM the cleanup is about to send.
            assert os.getpgid(descendant) == proc.pgid
            os.killpg(proc.pgid, signal.SIGTERM)
            await asyncio.sleep(0.3)
            assert _pid_alive(descendant), "fixture does not survive SIGTERM"

            result = await commands._probe_cleanup(None, proc)

            # Checked the instant cleanup returns: the claim under test is about
            # the state at *return time*, not after some later grace period.
            assert (result, _pid_alive(descendant)) == (None, False)
        finally:
            for killer, target in ((os.killpg, proc.pgid), (os.kill, descendant)):
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    killer(target, signal.SIGKILL)

    asyncio.run(case())


def test_b1_an_unavailable_sdk_never_leaves_a_spawned_child(monkeypatch):
    """B1 — SDK/client/driver setup refusal cannot leak a child.

    ``NativeAcpClient.__init__`` calls ``require_sdk()``, so an environment
    without the optional SDK fails *after* a naive spawn. Setup therefore
    happens before anything is started: the categorical result is returned with
    no child in existence at all.
    """
    from agent_run_supervisor.native_acp import NativeSdkUnavailableError

    spawned: list[object] = []

    async def never(*args, **kwargs):
        spawned.append(object())
        raise AssertionError("no child may be spawned before setup succeeds")

    monkeypatch.setattr(commands, "spawn_managed_process", never)
    monkeypatch.setattr(
        commands,
        "_probe_client",
        lambda instance: (_ for _ in ()).throw(
            NativeSdkUnavailableError("no sdk")
        ),
    )

    report = asyncio.run(commands._probe_initialize(_probe_instance(), _probe_env()))
    assert report["outcome"] == commands.PROBE_FAILED
    assert report["code"] == commands.ACP_SDK_UNAVAILABLE
    assert spawned == []


def test_b2_an_sdk_exception_during_the_probe_stays_categorical(monkeypatch):
    """The probe reports a stable code, never SDK or OS text.

    The root SDK exception-detail redactor is the seam that keeps a dependency's
    own ``logging.exception`` from putting a traceback on the root logger; the
    probe's own report is categorical by construction.
    """
    capture = _Capture()
    root = logging.getLogger()
    root.addHandler(capture)
    previous = root.level
    root.setLevel(logging.DEBUG)

    async def failing_spawn(*args, **kwargs):
        logging.getLogger().error(
            "connect failed", exc_info=OSError(2, "no such file", PROBE_SENTINEL)
        )
        raise commands.ManagedProcessError("SPAWN_FAILED", "boom")

    monkeypatch.setattr(commands, "spawn_managed_process", failing_spawn)
    try:
        report = asyncio.run(
            commands._probe_initialize(_probe_instance(), _probe_env())
        )
    finally:
        root.removeHandler(capture)
        root.setLevel(previous)

    assert report["outcome"] == commands.PROBE_FAILED
    assert PROBE_SENTINEL not in json.dumps(report)
    # The SDK root-logging containment survives the guard removal: the record
    # keeps its message and the exception class, and drops the detail.
    assert PROBE_SENTINEL not in capture.text()
    assert "[exception detail redacted: FileNotFoundError]" in capture.text()


# -- A2: "reset" must mean the production writer's exact output ---------------
#
# The key set alone is not the schema. A hybrid can keep every reset key and put
# a value-bearing literal in a field the shape check never looked at — and the
# reset path is the one that *recomputes and publishes a digest* over the whole
# record. Classification therefore validates the closed semantic domain of every
# field the production ``EnvProjection`` writer emits, not just its key names.


def _env_of(**overrides) -> dict:
    resolved = spec.resolve_run_environment(
        arsd_env={"HOME": "/home/svc", "TOKEN_NAME": SENTINEL},
        profile=profile_mod.STANDARD_NATIVE_ACP_V1,
        entry=AgentEntry(
            agent_id="a-1",
            profile_id="standard-native-acp-v1",
            command="some-agent",
            env_passthrough=("TOKEN_NAME", "NEVER_SET_NAME"),
        ),
    )
    env = resolved.value_blind_projection().to_dict()
    env.update(overrides)
    return env


ENV_HYBRID_SENTINEL_SITES = [
    pytest.param({"redaction": SENTINEL}, id="redaction"),
    pytest.param({"mediation_id": SENTINEL}, id="mediation-id"),
    pytest.param({"declared_absent": [SENTINEL]}, id="declared-absent"),
]


@pytest.mark.parametrize("override", ENV_HYBRID_SENTINEL_SITES)
def test_a2_a_value_bearing_env_field_is_legacy_before_any_recomputation(
    tmp_path, capsys, override
):
    run_dir = reset_launch_record(tmp_path)
    path = run_dir / "launch.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["env"] = _env_of(**override)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    assert commands._classify_launch_schema(payload) == "legacy"
    code, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    assert code == 0
    assert report["schema"] == "legacy"
    assert SENTINEL not in json.dumps(report, sort_keys=True)
    assert "recomputed_launch_spec_hash" not in report


@pytest.mark.parametrize("field", ["name", "source"])
def test_a2_a_value_bearing_name_entry_is_legacy(tmp_path, capsys, field):
    run_dir = reset_launch_record(tmp_path)
    path = run_dir / "launch.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    env = _env_of()
    env["names"][0][field] = SENTINEL
    payload["env"] = env
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    assert commands._classify_launch_schema(payload) == "legacy"
    code, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    assert code == 0
    assert report["schema"] == "legacy"
    assert SENTINEL not in json.dumps(report, sort_keys=True)


def test_a2_the_production_projection_is_still_exact():
    """The tightening must not reject what the writer actually emits."""
    assert spec.env_projection_shape_is_exact(_env_of())


def test_a2_a_forged_precedence_is_not_a_production_projection():
    """``precedence`` is the winning layer of ``source``, not a free integer."""
    env = _env_of()
    env["names"][0]["precedence"] = 4
    assert not spec.env_projection_shape_is_exact(env)


def test_a2_a_forged_count_is_not_a_production_projection():
    env = _env_of()
    env["resolved_count"] = len(env["names"]) + 1
    assert not spec.env_projection_shape_is_exact(env)


def test_a2_an_unredacted_name_entry_is_not_a_production_projection():
    env = _env_of()
    env["names"][0]["redacted"] = False
    assert not spec.env_projection_shape_is_exact(env)


# -- ENV-01: declared_absent is the registry's own closed domain --------------
#
# ``declared_absent`` is derived from one place only: the entry's own
# ``env_passthrough``, filtered by which of those names the daemon's environment
# did not hold. The parser accepts that list only when its names are
# grammar-valid, unique, and at most ``MAX_ENV_PASSTHROUGH`` — so a subset of it
# is bounded and unique too, and a record that breaks either fact was not
# written by the production writer.
#
# Grammar alone was checked. A duplicate name, or thirty-three of them, passed
# straight through to the reset path — which is the path that recomputes and
# publishes the record's digest.


def _absent(names) -> dict:
    return _env_of(declared_absent=list(names))


def _distinct_names(count: int) -> list[str]:
    return [f"DECLARED_ABSENT_{index}" for index in range(count)]


def _inspect_record(tmp_path, capsys, env: dict):
    run_dir = reset_launch_record(tmp_path)
    path = run_dir / "launch.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["env"] = env
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    classified = commands._classify_launch_schema(payload)
    code, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    return classified, code, report


def test_env01_a_duplicated_declared_absent_name_is_not_a_production_record(
    tmp_path, capsys
):
    """The parser refuses a repeated pass-through name; so must the reader."""
    duplicated = ["NEVER_SET_NAME", "NEVER_SET_NAME"]

    classified, code, report = _inspect_record(tmp_path, capsys, _absent(duplicated))
    assert classified == "legacy"
    assert code == 0
    assert report["schema"] == "legacy"
    # Withheld, not merely unverified: the env block is neither returned nor
    # taken as digest material.
    assert "env" not in report
    assert "recomputed_launch_spec_hash" not in report

    assert spec.env_projection_shape_is_exact(_absent(duplicated)) is False


def test_env01_more_declared_absent_names_than_the_registry_accepts_is_not_reset(
    tmp_path, capsys
):
    """``MAX_ENV_PASSTHROUGH + 1`` distinct valid names cannot have been written."""
    from agent_run_supervisor.native_acp.agent_registration import MAX_ENV_PASSTHROUGH

    over = _distinct_names(MAX_ENV_PASSTHROUGH + 1)

    classified, code, report = _inspect_record(tmp_path, capsys, _absent(over))
    assert classified == "legacy"
    assert code == 0
    assert report["schema"] == "legacy"
    assert "env" not in report
    assert "recomputed_launch_spec_hash" not in report

    assert spec.env_projection_shape_is_exact(_absent(over)) is False


def test_env01_the_permitted_boundary_is_still_accepted(tmp_path, capsys):
    """The control: exactly the bound, unique and valid, stays a reset record.

    Tightening a reader is only correct if it still recognises everything the
    writer can produce. A rule that rejected the boundary would silently push
    real production records onto the withholding path.
    """
    from agent_run_supervisor.native_acp.agent_registration import MAX_ENV_PASSTHROUGH

    at_bound = _distinct_names(MAX_ENV_PASSTHROUGH)
    assert spec.env_projection_shape_is_exact(_absent(at_bound)) is True

    classified, code, report = _inspect_record(tmp_path, capsys, _absent(at_bound))
    assert classified == "reset"
    assert report["schema"] == "reset"
    assert report["env"]["declared_absent"] == at_bound
    assert report["recomputed_launch_spec_hash"]
    # The record's own seal no longer matches once ``env`` was edited; what this
    # control proves is that the record stayed on the *reset* path at all.
    assert code == 1


def test_env01_an_empty_declared_absent_list_is_still_accepted():
    """Declaring no pass-through names is the ordinary case, not a violation."""
    assert spec.env_projection_shape_is_exact(_absent([])) is True


# -- A3: a legacy report carries no raw field from the record -----------------


@pytest.mark.parametrize("field", ["profile_id", "profile_revision"])
def test_a3_no_raw_legacy_field_reaches_the_report(tmp_path, capsys, field):
    """``profile_id``/``profile_revision`` are untrusted bytes in a legacy record.

    They are free-form strings in a document ARS did not write under the reset
    contract, so a projected environment value can sit in either one. The legacy
    path withholds them categorically rather than copying them through.
    """
    run_dir = tmp_path / "legacy-run"
    run_dir.mkdir()
    (run_dir / "launch.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "command": "old-agent",
                "argv": ["old-agent", "--serve"],
                "profile_id": SENTINEL,
                "profile_revision": SENTINEL,
                "fixed_env": {"TOKEN_NAME": SENTINEL},
            }
        ),
        encoding="utf-8",
    )
    code, report = run_cli(["run", "inspect", "--run-dir", str(run_dir)], capsys)
    assert code == 0
    assert report["schema"] == "legacy"
    assert report.get(field) != SENTINEL
    assert SENTINEL not in json.dumps(report, sort_keys=True)

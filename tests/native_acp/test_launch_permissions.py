"""Profile-selected launch permission material, and its read-only slice.

An `agent`-mode edit can complete **without** emitting
``session/request_permission``, so ARS's completion backstop only ever sees the
violation once the file already exists, and detection after the side effect is
not prevention. A private per-Run configuration denying ``Write(**)`` and
``Shell(*)`` before the process starts is what would actually stop it.

**No registered profile selects a policy**, because the one registered backend's
environment key names an agent's whole configuration root rather than a
permission-only file, and per-Run material under such a key relocates and then
deletes the agent's own Session state — see
``test_cursor_cross_run_session_resume.py``. Everything below therefore runs
against explicitly selecting test profiles: the mechanism, its fail-closed
materialization, its bounded cleanup, and its evidence binding are all unchanged
and stay fully under test for a future profile that has evidence to select one.

The seam is deliberately narrow. A **profile** — not an agent id — may select
one closed, source-owned launch-permission policy id. Nothing here is a plugin
framework, a dynamic approval path, a path-level write policy, or a new write
capability: this slice compiles exactly one read-only document, and a Run whose
frozen grant asks for more than it can faithfully enforce is refused before the
child exists rather than quietly widened.

The ACP ``PermissionBridge`` and the post-completion violation detector stay
exactly as they were. This is a second, earlier line — not a replacement.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

pytest.importorskip("acp")

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.native_acp import launch_permissions as lp
from agent_run_supervisor.native_acp.profile import (
    CLAUDE_AGENT_ACP_COMPAT_V1,
    CURSOR_NATIVE_ACP_V1,
    STANDARD_NATIVE_ACP_V1,
    AgentInstance,
    ProfileRegistry,
    ProfileValidationError,
)

from .test_model_only_fidelity import (
    CURSOR_SCRIPT,
    _cursor_request,
    _model_only_entry,
    _model_only_profile,
)
from .test_run_task import HAPPY_SCRIPT, Harness, _run


def _policy_profile(**overrides):
    kwargs = dict(launch_permission_policy_id=lp.POLICY_DENY_WRITE_AND_SHELL_V1)
    kwargs.update(overrides)
    return _model_only_profile(**kwargs)


def _harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: dict, **kw):
    harness = Harness(tmp_path, monkeypatch, script)
    harness.registry = ProfileRegistry((_policy_profile(**kw),))
    harness.entry = _model_only_entry()
    return harness


def _material_dir(run_dir: Path) -> Path:
    return run_dir / lp.LAUNCH_PERMISSION_DIRNAME


# -- 1. no registered profile selects the backend ---------------------------


def test_no_registered_profile_selects_a_launch_permission_policy() -> None:
    """The mechanism is registered and selectable; nothing selects it.

    The Cursor profile did, and that is what broke cross-Run continuity: this
    backend's key names an agent's whole configuration root rather than a
    permission-only file, so per-Run material relocated and then deleted the
    agent's own Session state. See
    ``test_cursor_cross_run_session_resume.py`` for the contract and the pinned
    failure class.
    """
    assert CURSOR_NATIVE_ACP_V1.launch_permission_policy_id is None
    assert STANDARD_NATIVE_ACP_V1.launch_permission_policy_id is None
    assert CLAUDE_AGENT_ACP_COMPAT_V1.launch_permission_policy_id is None
    # Still a closed, registered, selectable set — the seams below are unchanged.
    assert lp.LAUNCH_PERMISSION_POLICY_IDS == frozenset(
        {lp.POLICY_DENY_WRITE_AND_SHELL_V1}
    )


def test_the_instance_reports_the_profile_policy_without_naming_an_agent() -> None:
    """The pair answers, so no runtime path branches on which agent it is."""
    instance = AgentInstance(profile=_policy_profile(), entry=_model_only_entry())
    assert instance.launch_permission_policy_id == lp.POLICY_DENY_WRITE_AND_SHELL_V1
    plain = AgentInstance(profile=_model_only_profile(), entry=_model_only_entry())
    assert plain.launch_permission_policy_id is None


def test_the_profile_snapshot_and_hash_cover_the_policy_id() -> None:
    # Emitted only when it deviates, so a profile that selects nothing keeps the
    # ``profile_hash`` it already has — that hash is Session identity. Dropping
    # the Cursor selection therefore moved Cursor's hash and, as these two
    # frozen values prove, nobody else's.
    assert "launch_permission_policy_id" not in CURSOR_NATIVE_ACP_V1.snapshot()
    assert "launch_permission_policy_id" not in STANDARD_NATIVE_ACP_V1.snapshot()
    assert "launch_permission_policy_id" not in CLAUDE_AGENT_ACP_COMPAT_V1.snapshot()
    assert STANDARD_NATIVE_ACP_V1.profile_hash() == (
        "fcf4d46c2c072ba9bd23b198beb096cb9748e62e8168c2a48e5c76432d55f9b9"
    )
    assert CLAUDE_AGENT_ACP_COMPAT_V1.profile_hash() == (
        "c9e9258bfcc01e2962b87466c803d0a3ae25a1676936864bdbd78b75a544a241"
    )
    # Selecting it really does move the selecting profile's own hash, which is
    # why removing a selection is a revision rather than a silent edit.
    assert _policy_profile().snapshot()["launch_permission_policy_id"] == (
        lp.POLICY_DENY_WRITE_AND_SHELL_V1
    )
    assert _policy_profile().profile_hash() != _model_only_profile().profile_hash()


def test_an_unregistered_policy_id_is_refused_at_profile_construction() -> None:
    with pytest.raises(ProfileValidationError):
        _model_only_profile(launch_permission_policy_id="write-everything-v9")


def test_the_policy_env_key_may_not_also_be_a_base_allowlist_name() -> None:
    """One environment key never has two owners."""
    with pytest.raises(ProfileValidationError):
        _policy_profile(base_allowlist=("PATH", lp.CURSOR_CONFIG_DIR_ENV))


# -- 2. the compiled document and its digest --------------------------------


def test_the_canonical_document_denies_write_and_shell_and_is_deterministic() -> None:
    first = lp.compile_policy_document(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",)
    )
    second = lp.compile_policy_document(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",)
    )
    assert first == second
    payload = json.loads(first)
    assert payload["permissions"]["deny"] == ["Shell(*)", "Write(**)"]
    # Ordinary workspace reading is untouched: nothing denies a read, and no
    # positive write/execute grant is invented.
    assert "allow" not in payload["permissions"]
    assert "Read" not in first


def test_the_digest_is_a_sha256_over_the_exact_document_bytes() -> None:
    import hashlib

    document = lp.compile_policy_document(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",)
    )
    expected = hashlib.sha256(document.encode("utf-8")).hexdigest()
    assert lp.policy_digest(document) == f"sha256:{expected}"
    assert lp.policy_digest(document) == lp.policy_digest(document)


@pytest.mark.parametrize(
    "capabilities",
    [("read", "write"), ("execute",), ("read", "delete"), ("move",), ("terminal",)],
)
def test_a_grant_this_backend_cannot_enforce_is_refused(capabilities) -> None:
    """Fail closed rather than silently widen.

    The read-only backend enforces exactly one thing. A grant that asks for a
    side-effecting capability is refused with a stable code, because emitting a
    deny-everything document for it would silently ignore what the caller froze.
    """
    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.compile_policy_document(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=capabilities
        )
    assert excinfo.value.code == lp.LAUNCH_PERMISSION_UNSUPPORTED_GRANT


@pytest.mark.parametrize("capabilities", [(), ("read",), ("read", "search")])
def test_a_read_only_grant_compiles(capabilities) -> None:
    assert lp.compile_policy_document(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=capabilities
    )


# -- 3. materialization is private and fail-closed --------------------------


def test_materialize_writes_a_private_directory_and_file(tmp_path: Path) -> None:
    material = lp.materialize(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
    )

    directory = _material_dir(tmp_path)
    config = directory / lp.LAUNCH_PERMISSION_FILENAME
    assert directory.is_dir()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert config.read_text(encoding="utf-8") == lp.compile_policy_document(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",)
    )
    assert material.digest == lp.policy_digest(config.read_text(encoding="utf-8"))
    assert material.env_pairs == ((lp.CURSOR_CONFIG_DIR_ENV, str(directory)),)


def test_materialize_refuses_an_existing_directory(tmp_path: Path) -> None:
    _material_dir(tmp_path).mkdir()
    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.materialize(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
        )
    assert excinfo.value.code == lp.LAUNCH_PERMISSION_MATERIALIZE_FAILED


def test_materialize_refuses_a_symlinked_target(tmp_path: Path) -> None:
    elsewhere = tmp_path / "attacker-owned"
    elsewhere.mkdir()
    _material_dir(tmp_path).symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.materialize(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
        )
    assert excinfo.value.code == lp.LAUNCH_PERMISSION_MATERIALIZE_FAILED
    # Nothing was written through the symlink.
    assert list(elsewhere.iterdir()) == []


def test_a_refusal_carries_no_path_or_exception_text(tmp_path: Path) -> None:
    _material_dir(tmp_path).mkdir()
    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.materialize(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
        )
    text = str(excinfo.value)
    assert text == lp.LAUNCH_PERMISSION_MATERIALIZE_FAILED
    assert str(tmp_path) not in text
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


# -- 4. discard removes everything, including what the child wrote ----------


def test_discard_removes_the_material_and_child_written_files(tmp_path: Path) -> None:
    material = lp.materialize(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
    )
    directory = _material_dir(tmp_path)
    # Cursor treats this as its config home and may write its own state there.
    (directory / "cursor-state.json").write_text("{}", encoding="utf-8")
    (directory / "nested").mkdir()
    (directory / "nested" / "more.log").write_text("x", encoding="utf-8")

    lp.discard(material)

    assert not directory.exists()
    assert tmp_path.is_dir()


def test_discard_is_idempotent(tmp_path: Path) -> None:
    material = lp.materialize(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
    )
    lp.discard(material)
    lp.discard(material)
    assert not _material_dir(tmp_path).exists()


def test_discard_unlinks_a_symlink_inside_rather_than_following_it(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    material = lp.materialize(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
    )
    (_material_dir(tmp_path) / "escape").symlink_to(outside, target_is_directory=True)

    lp.discard(material)

    assert not _material_dir(tmp_path).exists()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_a_cleanup_failure_is_categorical(tmp_path: Path, monkeypatch) -> None:
    material = lp.materialize(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
    )

    def boom(*args, **kwargs):
        raise OSError(13, "Permission denied", str(tmp_path / "secret"))

    monkeypatch.setattr(lp.os, "unlink", boom)

    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.discard(material)
    assert str(excinfo.value) == lp.LAUNCH_PERMISSION_CLEANUP_FAILED
    assert str(tmp_path) not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


# -- 5. the Run path -------------------------------------------------------


def test_the_config_dir_reaches_the_child_and_its_value_stays_withheld(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child really receives it; no ARS sink carries the value.

    ``env_probe`` writes the raw value to a test-owned file **outside** the Run
    tree and makes the final message value-free, so the child's receipt is
    proven without asking an ARS artifact to carry the path.
    """
    probe = tmp_path / "child-env-probe.txt"
    script = dict(CURSOR_SCRIPT)
    script["env_probe"] = {"name": lp.CURSOR_CONFIG_DIR_ENV, "path": str(probe)}
    harness = _harness(tmp_path, monkeypatch, script)

    result = _run(harness.task(request=_cursor_request()))

    assert result.status is AgentRunStatus.COMPLETED
    run_dir = harness.run_dir()
    observed = probe.read_text(encoding="utf-8").strip()
    assert observed, "the child never saw CURSOR_CONFIG_DIR"
    assert observed == str(_material_dir(run_dir))

    payload = json.loads((run_dir / "result.json").read_text())
    assert payload["final_message"] == "ENV_PROBE:PRESENT"

    # The value is nowhere ARS wrote; only the name and its source class are.
    for artifact in ("spec.json", "launch.json", "events.jsonl", "result.json",
                     "stderr.log", "effective.json"):
        raw = (run_dir / artifact).read_bytes()
        assert observed.encode("utf-8") not in raw, artifact
    launch = json.loads((run_dir / "launch.json").read_text())
    projected = {item["name"]: item for item in launch["env"]["names"]}
    assert lp.CURSOR_CONFIG_DIR_ENV in projected
    assert projected[lp.CURSOR_CONFIG_DIR_ENV]["source"] == "launch_permission"
    assert projected[lp.CURSOR_CONFIG_DIR_ENV]["redacted"] is True


def test_launch_evidence_binds_the_policy_by_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))

    result = _run(harness.task(request=_cursor_request()))

    assert result.status is AgentRunStatus.COMPLETED
    launch = json.loads((harness.run_dir() / "launch.json").read_text())
    assert launch["launch_permission_policy_id"] == lp.POLICY_DENY_WRITE_AND_SHELL_V1
    assert launch["launch_permission_digest"] == lp.policy_digest(
        lp.compile_policy_document(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",)
        )
    )
    # A digest, not a location and not the document.
    assert lp.LAUNCH_PERMISSION_DIRNAME not in json.dumps(launch)
    assert "Write(**)" not in json.dumps(launch)


def test_a_non_read_only_grant_fails_before_spawn_and_before_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))

    result = _run(
        harness.task(
            request=_cursor_request(grant_capabilities=("read", "write"))
        )
    )

    assert result.status is AgentRunStatus.FAILED
    payload = json.loads((harness.run_dir() / "result.json").read_text())
    assert payload["detail_code"] == lp.LAUNCH_PERMISSION_UNSUPPORTED_GRANT
    assert not (harness.run_dir() / "prompt-dispatch-started").exists()
    assert harness.methods_seen() == []
    assert not _material_dir(harness.run_dir()).exists()
    # A clean pre-dispatch refusal leaves the Session reusable.
    assert harness.session_store().open_session("sess-native-1").quarantine is None


def test_material_is_cleaned_after_a_completed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))

    result = _run(harness.task(request=_cursor_request()))

    assert result.status is AgentRunStatus.COMPLETED
    assert not _material_dir(harness.run_dir()).exists()
    # The Run's own durable evidence is untouched by the cleanup.
    assert (harness.run_dir() / "result.json").exists()
    assert (harness.run_dir() / "launch.json").exists()


def test_cleanup_never_runs_before_the_child_is_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the material is that a live child can consult it."""
    from agent_run_supervisor.native_acp.run_task import RunTask

    observed: list[bool] = []
    original = RunTask._cleanup_launch_permissions

    def spy(self, ctx):
        observed.append(ctx.proc is None or ctx.exit_state is not None)
        return original(self, ctx)

    monkeypatch.setattr(RunTask, "_cleanup_launch_permissions", spy)
    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))

    assert _run(harness.task(request=_cursor_request())).status is (
        AgentRunStatus.COMPLETED
    )
    assert observed, "cleanup never ran"
    assert all(observed), "cleanup ran while an un-reaped child could still read it"


def test_material_is_cleaned_after_a_spawn_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))

    async def boom(**kwargs):
        del kwargs
        raise OSError(2, "No such file or directory", "/bin/nope")

    monkeypatch.setattr(
        "agent_run_supervisor.native_acp.run_task.spawn_managed_process", boom
    )

    result = _run(harness.task(request=_cursor_request()))

    assert result.status is AgentRunStatus.FAILED
    assert not _material_dir(harness.run_dir()).exists()


def test_material_is_cleaned_after_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = dict(CURSOR_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = _harness(tmp_path, monkeypatch, script)
    task = harness.task(request=_cursor_request())

    async def case():
        runner = asyncio.ensure_future(task.run())
        for _ in range(400):
            await asyncio.sleep(0.01)
            if (harness.run_dir() / "prompt-dispatch-started").exists():
                break
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner

    asyncio.run(case())

    assert not _material_dir(harness.run_dir()).exists()


def test_material_is_cleaned_after_a_turn_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.native_acp.spec import RunLimits

    script = dict(CURSOR_SCRIPT)
    script["hang_prompt_until_cancel"] = True
    harness = _harness(tmp_path, monkeypatch, script)

    result = _run(
        harness.task(
            request=_cursor_request(
                limits=RunLimits(turn_timeout_seconds=1.0, cancel_grace_seconds=1.0)
            )
        )
    )

    assert result.status in (AgentRunStatus.UNKNOWN, AgentRunStatus.TIMED_OUT)
    assert not _material_dir(harness.run_dir()).exists()


def test_a_cleanup_failure_during_a_run_is_recorded_categorically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Housekeeping that failed is stated, not swallowed and not leaked."""
    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))

    def boom(material):
        raise lp.LaunchPermissionError(lp.LAUNCH_PERMISSION_CLEANUP_FAILED)

    monkeypatch.setattr(
        "agent_run_supervisor.native_acp.run_task.discard_launch_permissions", boom
    )

    result = _run(harness.task(request=_cursor_request()))

    # The Run's own terminal is unaffected: a leftover private directory is
    # hygiene, not a supervision fact.
    assert result.status is AgentRunStatus.COMPLETED
    run_dir = harness.run_dir()
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text().splitlines()
    ]
    families = [event["type"] for event in events]
    assert "launch_permission_cleanup_failed" in families
    failure = [e for e in events if e["type"] == "launch_permission_cleanup_failed"][0]
    assert set(failure) == {"seq", "type"}
    assert str(_material_dir(run_dir)) not in json.dumps(events)


def test_material_is_cleaned_after_an_emergency_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.native_acp.run_task import RunTask

    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))

    async def boom(self, ctx):
        del self, ctx
        raise RuntimeError("inner finalization failed")

    monkeypatch.setattr(RunTask, "_finalize_inner", boom)

    result = _run(harness.task(request=_cursor_request()))

    assert result.status is AgentRunStatus.FAILED
    assert not _material_dir(harness.run_dir()).exists()


# -- 6. profiles that select nothing are untouched --------------------------


def test_a_default_profile_creates_no_material_and_keeps_its_env_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path, monkeypatch, dict(HAPPY_SCRIPT))

    result = _run(harness.task())

    assert result.status is AgentRunStatus.COMPLETED
    run_dir = harness.run_dir()
    assert not _material_dir(run_dir).exists()
    launch = json.loads((run_dir / "launch.json").read_text())
    assert "launch_permission_policy_id" not in launch
    assert "launch_permission_digest" not in launch
    names = {item["name"] for item in launch["env"]["names"]}
    assert lp.CURSOR_CONFIG_DIR_ENV not in names
    assert all(
        item["source"] != "launch_permission" for item in launch["env"]["names"]
    )


def test_the_material_lives_inside_the_supervisor_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ARS-owned writable surfaces stay exactly two.

    The private config is created under the Run directory, through the same
    supervisor root ARS already owns — not in `$HOME`, not in the project, and
    not in a third writable surface.
    """
    captured: list[Path] = []
    from agent_run_supervisor.native_acp.run_task import RunTask

    original = RunTask._materialize_launch_permissions

    def spy(self, ctx, instance, capabilities):
        material = original(self, ctx, instance, capabilities)
        if material is not None:
            captured.append(Path(material.directory))
        return material

    monkeypatch.setattr(RunTask, "_materialize_launch_permissions", spy)
    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))

    _run(harness.task(request=_cursor_request()))

    assert captured
    assert captured[0].is_relative_to(harness.root / "native-runs")
    assert not captured[0].is_relative_to(Path(os.path.expanduser("~")) / ".cursor")


# ===========================================================================
# Repair coverage — five accepted blockers
# ===========================================================================


# -- B1: an exclusive-create refusal must not delete what it did not make ---


@pytest.mark.parametrize("shape", ["directory", "file", "symlink"])
def test_a_refused_materialization_leaves_a_pre_existing_target_untouched(
    tmp_path: Path, shape: str
) -> None:
    """Refusal is a refusal, not a deletion.

    ``mkdir`` is the exclusive gate precisely so a pre-existing target is never
    adopted. Cleaning up after that refusal would delete something this
    invocation never created — the one thing an exclusive create must not do.
    Only material whose directory this call made is this call's to remove.
    """
    target = _material_dir(tmp_path)
    outside = tmp_path / "planted-elsewhere"
    if shape == "directory":
        target.mkdir()
        (target / "planted.txt").write_text("PLANTED", encoding="utf-8")
    elif shape == "file":
        target.write_text("PLANTED", encoding="utf-8")
    else:
        outside.mkdir()
        (outside / "planted.txt").write_text("PLANTED", encoding="utf-8")
        target.symlink_to(outside, target_is_directory=True)

    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.materialize(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
        )

    assert excinfo.value.code == lp.LAUNCH_PERMISSION_MATERIALIZE_FAILED
    assert os.path.lexists(target), f"{shape} target was deleted by a refusal"
    if shape == "directory":
        assert (target / "planted.txt").read_text(encoding="utf-8") == "PLANTED"
    elif shape == "file":
        assert target.read_text(encoding="utf-8") == "PLANTED"
    else:
        assert target.is_symlink()
        assert (outside / "planted.txt").read_text(encoding="utf-8") == "PLANTED"


# -- B2: the digest binds exactly the bytes that were fully written ---------


def _partial_write(monkeypatch, payload: bytes, *, first: int, then: int | None):
    """Patch ``os.write`` for exactly this payload, delegating everything else.

    Scoped by content rather than by descriptor: the compiled document is a
    49-byte literal nothing else in the process writes, so unrelated writes —
    pytest's own included — go straight to the real call.
    """
    real_write = os.write

    def fake(fd, data):
        view = bytes(data)
        if view == payload:
            return real_write(fd, view[:first]) if first else 0
        if view == payload[first:] and then is not None:
            return real_write(fd, view[:then]) if then else 0
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", fake)


def test_a_short_write_fails_closed_instead_of_digesting_bytes_it_never_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One byte on disk and a digest for forty-nine is a lie about the policy.

    ``os.write`` may legally write fewer bytes than it was given. Ignoring the
    count produces material whose durable digest describes a document the child
    will never read.
    """
    payload = lp.compile_policy_document(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",)
    ).encode("utf-8")
    _partial_write(monkeypatch, payload, first=1, then=0)

    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.materialize(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
        )

    assert excinfo.value.code == lp.LAUNCH_PERMISSION_MATERIALIZE_FAILED
    # Partial material this invocation created is its own to remove.
    assert not _material_dir(tmp_path).exists()


def test_zero_progress_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = lp.compile_policy_document(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",)
    ).encode("utf-8")
    _partial_write(monkeypatch, payload, first=0, then=None)

    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.materialize(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
        )

    assert excinfo.value.code == lp.LAUNCH_PERMISSION_MATERIALIZE_FAILED
    assert not _material_dir(tmp_path).exists()


def test_a_partial_write_that_keeps_progressing_still_writes_every_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Short writes are legal, so the loop must finish the document."""
    payload = lp.compile_policy_document(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",)
    ).encode("utf-8")
    real_write = os.write

    def fake(fd, data):
        view = bytes(data)
        if view and payload.endswith(view):
            return real_write(fd, view[:1])
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", fake)

    material = lp.materialize(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
    )

    written = (_material_dir(tmp_path) / lp.LAUNCH_PERMISSION_FILENAME).read_bytes()
    assert written == payload
    assert material.digest == lp.policy_digest(payload.decode("utf-8"))


# -- B3: the launch evidence pair is bound, not merely carried --------------


def _snapshot(**overrides):
    from agent_run_supervisor.native_acp.spec import EnvName, EnvProjection, LaunchSnapshot

    names = overrides.pop("names", ())
    kwargs = dict(
        command="some-agent",
        argv=("some-agent",),
        profile_id="cursor-native-acp-v1",
        profile_revision=1,
        profile_hash="0" * 64,
        agent_id="a-1",
        env=EnvProjection(resolved_count=len(names), names=tuple(names)),
    )
    kwargs.update(overrides)
    return LaunchSnapshot(**kwargs), EnvName


def _policy_env_name(**kw):
    from agent_run_supervisor.native_acp.spec import EnvName

    body = dict(
        name=lp.CURSOR_CONFIG_DIR_ENV,
        source=lp.ENV_SOURCE_LAUNCH_PERMISSION,
        precedence=5,
    )
    body.update(kw)
    return EnvName(**body)


# The canonical digest of the registered policy's own document. An arbitrary
# well-shaped digest is not "exact": the document is fixed source bytes, so
# there is exactly one digest a truthful record can carry.
_GOOD_DIGEST = lp.canonical_policy_digest(lp.POLICY_DENY_WRITE_AND_SHELL_V1)
_WRONG_DIGEST = "sha256:" + "b" * 64


def test_a_policy_id_without_a_digest_is_refused_at_construction() -> None:
    from agent_run_supervisor.native_acp.spec import SpecValidationError

    with pytest.raises(SpecValidationError):
        _snapshot(
            launch_permission_policy_id=lp.POLICY_DENY_WRITE_AND_SHELL_V1,
            names=(_policy_env_name(),),
        )


def test_a_digest_without_a_policy_id_is_refused_at_construction() -> None:
    from agent_run_supervisor.native_acp.spec import SpecValidationError

    with pytest.raises(SpecValidationError):
        _snapshot(launch_permission_digest=_GOOD_DIGEST)


@pytest.mark.parametrize(
    "digest",
    ["sha256:" + "A" * 64, "sha256:" + "a" * 63, "a" * 64, "sha512:" + "a" * 64, ""],
)
def test_a_non_canonical_digest_is_refused_at_construction(digest) -> None:
    from agent_run_supervisor.native_acp.spec import SpecValidationError

    with pytest.raises(SpecValidationError):
        _snapshot(
            launch_permission_policy_id=lp.POLICY_DENY_WRITE_AND_SHELL_V1,
            launch_permission_digest=digest,
            names=(_policy_env_name(),),
        )


def test_an_unregistered_policy_id_is_refused_at_construction() -> None:
    from agent_run_supervisor.native_acp.spec import SpecValidationError

    with pytest.raises(SpecValidationError):
        _snapshot(
            launch_permission_policy_id="write-everything-v9",
            launch_permission_digest=_GOOD_DIGEST,
            names=(_policy_env_name(),),
        )


def test_the_pair_requires_its_matching_environment_projection() -> None:
    from agent_run_supervisor.native_acp.spec import SpecValidationError

    # Present pair, absent projection.
    with pytest.raises(SpecValidationError):
        _snapshot(
            launch_permission_policy_id=lp.POLICY_DENY_WRITE_AND_SHELL_V1,
            launch_permission_digest=_GOOD_DIGEST,
        )
    # Present pair, wrong name.
    with pytest.raises(SpecValidationError):
        _snapshot(
            launch_permission_policy_id=lp.POLICY_DENY_WRITE_AND_SHELL_V1,
            launch_permission_digest=_GOOD_DIGEST,
            names=(_policy_env_name(name="SOMETHING_ELSE"),),
        )
    # Absent pair, present projection.
    with pytest.raises(SpecValidationError):
        _snapshot(names=(_policy_env_name(),))


def test_the_matching_pair_and_projection_construct_and_round_trip() -> None:
    from agent_run_supervisor.native_acp.spec import launch_payload_shape_is_exact

    snapshot, _ = _snapshot(
        launch_permission_policy_id=lp.POLICY_DENY_WRITE_AND_SHELL_V1,
        launch_permission_digest=_GOOD_DIGEST,
        names=(_policy_env_name(),),
    )
    payload = snapshot.to_dict()
    payload["launch_spec_hash"] = snapshot.launch_hash()
    assert launch_payload_shape_is_exact(payload)


def _reader_payload():
    from agent_run_supervisor.native_acp.spec import launch_payload_shape_is_exact

    snapshot, _ = _snapshot(
        launch_permission_policy_id=lp.POLICY_DENY_WRITE_AND_SHELL_V1,
        launch_permission_digest=_GOOD_DIGEST,
        names=(_policy_env_name(),),
    )
    payload = snapshot.to_dict()
    payload["launch_spec_hash"] = snapshot.launch_hash()
    assert launch_payload_shape_is_exact(payload)
    return payload, launch_payload_shape_is_exact


def test_the_reader_refuses_a_half_pair_or_an_invalid_one() -> None:
    payload, reader = _reader_payload()

    missing_digest = dict(payload)
    missing_digest.pop("launch_permission_digest")
    assert not reader(missing_digest)

    missing_id = dict(payload)
    missing_id.pop("launch_permission_policy_id")
    assert not reader(missing_id)

    bad_digest = dict(payload, launch_permission_digest="sha256:" + "z" * 64)
    assert not reader(bad_digest)

    bad_policy = dict(payload, launch_permission_policy_id="write-everything-v9")
    assert not reader(bad_policy)


def test_the_reader_refuses_a_pair_whose_environment_projection_disagrees() -> None:
    payload, reader = _reader_payload()

    stripped = json.loads(json.dumps(payload))
    stripped["env"]["names"] = []
    stripped["env"]["resolved_count"] = 0
    assert not reader(stripped)

    renamed = json.loads(json.dumps(payload))
    renamed["env"]["names"][0]["name"] = "PATH"
    assert not reader(renamed)

    resourced = json.loads(json.dumps(payload))
    resourced["env"]["names"][0]["source"] = "overlay"
    resourced["env"]["names"][0]["precedence"] = 3
    assert not reader(resourced)


def test_the_reader_refuses_a_launch_permission_name_with_no_pair() -> None:
    from agent_run_supervisor.native_acp.spec import launch_payload_shape_is_exact

    payload, _ = _reader_payload()
    orphan = json.loads(json.dumps(payload))
    orphan.pop("launch_permission_policy_id")
    orphan.pop("launch_permission_digest")
    assert not launch_payload_shape_is_exact(orphan)


def _assembler(tmp_path: Path, profile):
    import os as _os

    from agent_run_supervisor.native_acp.spec import (
        RunSpecAssembler,
        resolve_run_environment,
    )
    from .test_model_only_fidelity import _cursor_request, _model_only_entry

    entry = _model_only_entry()
    registry = ProfileRegistry((profile,))
    assembler = RunSpecAssembler(_cursor_request())
    instance = assembler.resolve_agent(entry, registry=registry)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    assembler.bind_workspace(root=workspace, cwd=None)
    return assembler, instance, entry, resolve_run_environment, dict(_os.environ)


def test_the_assembler_refuses_a_selecting_profile_with_no_material(
    tmp_path: Path,
) -> None:
    from agent_run_supervisor.native_acp.spec import SpecValidationError

    assembler, instance, entry, resolve, env = _assembler(tmp_path, _policy_profile())
    environment = resolve(arsd_env=env, profile=instance.profile, entry=entry)

    with pytest.raises(SpecValidationError):
        assembler.resolve_launch(environment=environment, launch_permission=None)


def test_the_assembler_refuses_material_for_a_non_selecting_profile(
    tmp_path: Path,
) -> None:
    from agent_run_supervisor.native_acp.spec import SpecValidationError

    assembler, instance, entry, resolve, env = _assembler(
        tmp_path, _model_only_profile()
    )
    material = lp.materialize(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
    )
    environment = resolve(
        arsd_env=env,
        profile=instance.profile,
        entry=entry,
        launch_permission=material.env_pairs,
    )

    with pytest.raises(SpecValidationError):
        assembler.resolve_launch(environment=environment, launch_permission=material)


def test_the_assembler_refuses_a_policy_id_the_profile_did_not_select(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dataclasses

    from agent_run_supervisor.native_acp.spec import SpecValidationError

    assembler, instance, entry, resolve, env = _assembler(tmp_path, _policy_profile())
    material = lp.materialize(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
    )
    mismatched = dataclasses.replace(material, policy_id="write-everything-v9")
    environment = resolve(
        arsd_env=env,
        profile=instance.profile,
        entry=entry,
        launch_permission=material.env_pairs,
    )

    with pytest.raises(SpecValidationError):
        assembler.resolve_launch(environment=environment, launch_permission=mismatched)


def test_the_assembler_refuses_material_the_environment_does_not_carry(
    tmp_path: Path,
) -> None:
    from agent_run_supervisor.native_acp.spec import SpecValidationError

    assembler, instance, entry, resolve, env = _assembler(tmp_path, _policy_profile())
    material = lp.materialize(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
    )
    # Material compiled, but layer 5 never projected: the child would never see
    # the policy while the sealed evidence claims it did.
    environment = resolve(arsd_env=env, profile=instance.profile, entry=entry)

    with pytest.raises(SpecValidationError):
        assembler.resolve_launch(environment=environment, launch_permission=material)


# -- B4: enumeration is bounded, not bounded-after-the-fact -----------------


class _ExplodingEntry:
    def __init__(self, path: Path, index: int) -> None:
        self.name = f"entry-{index}"
        self.path = str(path / self.name)

    def is_dir(self, follow_symlinks: bool = True) -> bool:
        return False


class _ExplodingScandir:
    """Yields one entry past the bound, then refuses to be advanced further.

    A directory a child grew is not bounded by anything ARS controls, so the
    finalizer must stop *while iterating* rather than after materializing the
    whole listing. Consuming this iterator past the bound is the defect.
    """

    def __init__(self, path: Path, limit: int) -> None:
        self._path = path
        self._limit = limit
        self.advanced = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return self

    def __next__(self):
        if self.advanced > self._limit:
            raise AssertionError(
                "cleanup consumed the directory iterator past its budget"
            )
        self.advanced += 1
        return _ExplodingEntry(self._path, self.advanced)


def test_cleanup_stops_iterating_at_its_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    material = lp.materialize(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
    )
    directory = _material_dir(tmp_path)
    real_scandir = os.scandir
    listing = _ExplodingScandir(directory, lp.MAX_CLEANUP_ENTRIES + 1)

    def fake_scandir(path=".", *args, **kwargs):
        if str(path) == str(directory):
            return listing
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    monkeypatch.setattr(os, "unlink", lambda *a, **k: None)

    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.discard(material)

    assert excinfo.value.code == lp.LAUNCH_PERMISSION_CLEANUP_FAILED
    assert listing.advanced <= lp.MAX_CLEANUP_ENTRIES + 1


# -- B5: a failed cleanup is durably classified on every reachable path -----


def _fail_cleanup(monkeypatch) -> None:
    def boom(material):
        raise lp.LaunchPermissionError(lp.LAUNCH_PERMISSION_CLEANUP_FAILED)

    monkeypatch.setattr(
        "agent_run_supervisor.native_acp.run_task.discard_launch_permissions", boom
    )


def _cleanup_marker(run_dir: Path) -> Path:
    return run_dir / lp.LAUNCH_PERMISSION_CLEANUP_MARKER


def _assert_categorical_marker(run_dir: Path, *, secret: str | None = None) -> None:
    marker = _cleanup_marker(run_dir)
    assert marker.exists(), "a durable cleanup failure was never classified"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["code"] == lp.LAUNCH_PERMISSION_CLEANUP_FAILED
    assert set(payload) == {"code", "run_id"}
    rendered = marker.read_text(encoding="utf-8")
    assert lp.LAUNCH_PERMISSION_DIRNAME not in rendered
    assert str(run_dir) not in rendered
    assert "Write(**)" not in rendered
    if secret is not None:
        assert secret not in rendered


def test_a_cleanup_failure_before_any_writer_is_still_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-spawn refusal: no EventWriter exists, so an event alone vanishes."""
    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))
    _fail_cleanup(monkeypatch)

    async def never(**kwargs):
        del kwargs
        raise AssertionError("no child may be spawned on this path")

    monkeypatch.setattr(
        "agent_run_supervisor.native_acp.run_task.spawn_managed_process", never
    )
    monkeypatch.setattr(
        "agent_run_supervisor.native_acp.run_task.RunTask._bind_session",
        lambda self, ctx, spec, binding, instance: (_ for _ in ()).throw(
            RuntimeError("session binding failed")
        ),
    )

    result = _run(harness.task(request=_cursor_request()))

    assert result.status in (AgentRunStatus.FAILED, AgentRunStatus.UNKNOWN)
    _assert_categorical_marker(harness.run_dir())


def test_a_cleanup_failure_after_a_spawn_failure_is_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))
    _fail_cleanup(monkeypatch)

    async def boom(**kwargs):
        del kwargs
        raise OSError(2, "No such file or directory", "/bin/nope")

    monkeypatch.setattr(
        "agent_run_supervisor.native_acp.run_task.spawn_managed_process", boom
    )

    result = _run(harness.task(request=_cursor_request()))

    assert result.status is AgentRunStatus.FAILED
    _assert_categorical_marker(harness.run_dir())


def test_a_cleanup_failure_on_the_emergency_path_is_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_run_supervisor.native_acp.run_task import RunTask

    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))
    _fail_cleanup(monkeypatch)

    async def boom(self, ctx):
        del self, ctx
        raise RuntimeError("inner finalization failed")

    monkeypatch.setattr(RunTask, "_finalize_inner", boom)

    result = _run(harness.task(request=_cursor_request()))

    assert result.status is AgentRunStatus.FAILED
    _assert_categorical_marker(harness.run_dir())


def test_a_cleanup_failure_on_a_completed_run_is_durable_and_leak_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARS_LP_SECRET", "cleanup-secret-4b71")
    harness = _harness(
        tmp_path,
        monkeypatch,
        dict(CURSOR_SCRIPT),
        base_allowlist=(
            "PATH",
            "HOME",
            "FAKE_AGENT_SCRIPT",
            "FAKE_AGENT_TRACE",
            "ARS_LP_SECRET",
        ),
    )
    _fail_cleanup(monkeypatch)

    result = _run(harness.task(request=_cursor_request()))

    assert result.status is AgentRunStatus.COMPLETED
    _assert_categorical_marker(harness.run_dir(), secret="cleanup-secret-4b71")


def test_a_later_successful_cleanup_does_not_erase_the_recorded_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The outer last resort retries; it must not rewrite history.

    A first attempt that failed is a durable fact even when a second attempt
    then succeeds — the operator still needs to know the finalizer could not
    complete on its first, in-order pass.
    """
    from agent_run_supervisor.native_acp.run_task import RunTask

    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))
    calls: list[int] = []
    real = lp.discard

    def flaky(material):
        calls.append(1)
        if len(calls) == 1:
            raise lp.LaunchPermissionError(lp.LAUNCH_PERMISSION_CLEANUP_FAILED)
        return real(material)

    monkeypatch.setattr(
        "agent_run_supervisor.native_acp.run_task.discard_launch_permissions", flaky
    )

    result = _run(harness.task(request=_cursor_request()))

    assert result.status is AgentRunStatus.COMPLETED
    assert len(calls) >= 2, "the outer last resort never retried"
    assert not _material_dir(harness.run_dir()).exists()
    _assert_categorical_marker(harness.run_dir())


def test_a_clean_run_writes_no_cleanup_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))

    assert _run(harness.task(request=_cursor_request())).status is (
        AgentRunStatus.COMPLETED
    )
    assert not _cleanup_marker(harness.run_dir()).exists()


# ===========================================================================
# Follow-up repair — two demonstrated sibling gaps
# ===========================================================================


# -- A: the reserved name is owned by its layer, exclusively ----------------


_RELABELLED_SOURCES = [("base", 1), ("passthrough", 2), ("overlay", 3), ("mediation", 4)]


def _named(name: str, source: str, precedence: int):
    from agent_run_supervisor.native_acp.spec import EnvName

    return EnvName(name=name, source=source, precedence=precedence)


def _payload_with_names(names, **pair):
    """A launch payload built by hand, so an invalid one can still be read back.

    The constructor refuses what the reader must also refuse, so a reader test
    cannot obtain its own subject through ``LaunchSnapshot``. This composes the
    document the way the writer would and then says what the writer never would.
    """
    from agent_run_supervisor.native_acp.spec import EnvProjection, LaunchSnapshot

    clean, _ = _snapshot()
    payload = clean.to_dict()
    payload["launch_spec_hash"] = clean.launch_hash()
    projection = EnvProjection(resolved_count=len(names), names=tuple(names))
    payload["env"] = projection.to_dict()
    for key, value in pair.items():
        payload[key] = value
    return payload


@pytest.mark.parametrize("source,precedence", _RELABELLED_SOURCES)
def test_a_relabelled_reserved_name_is_refused_at_construction(
    source: str, precedence: int
) -> None:
    """Relabelling the source must not launder the reserved key.

    With no policy pair, calling the reserved name ``base`` or ``overlay``
    claims an operator supplied it — which the profile invariant forbids and
    the projection would never produce. Filtering only by source let exactly
    that through, so the check is now *name-first*: a reserved key is owned by
    the launch-permission layer and by nothing else.
    """
    from agent_run_supervisor.native_acp.spec import SpecValidationError

    with pytest.raises(SpecValidationError):
        _snapshot(names=(_named(lp.CURSOR_CONFIG_DIR_ENV, source, precedence),))


@pytest.mark.parametrize("source,precedence", _RELABELLED_SOURCES)
def test_a_relabelled_reserved_name_is_refused_by_the_reader(
    source: str, precedence: int
) -> None:
    from agent_run_supervisor.native_acp.spec import launch_payload_shape_is_exact

    payload = _payload_with_names(
        (_named(lp.CURSOR_CONFIG_DIR_ENV, source, precedence),)
    )
    assert not launch_payload_shape_is_exact(payload)


@pytest.mark.parametrize("source,precedence", _RELABELLED_SOURCES)
def test_a_present_pair_with_the_name_relabelled_is_refused(
    source: str, precedence: int
) -> None:
    """Present pair, reserved name under the wrong layer: still inconsistent."""
    from agent_run_supervisor.native_acp.spec import SpecValidationError

    with pytest.raises(SpecValidationError):
        _snapshot(
            launch_permission_policy_id=lp.POLICY_DENY_WRITE_AND_SHELL_V1,
            launch_permission_digest=_GOOD_DIGEST,
            names=(_named(lp.CURSOR_CONFIG_DIR_ENV, source, precedence),),
        )


def test_a_relabelled_duplicate_alongside_the_real_entry_is_refused() -> None:
    """One reserved key, one owner, one entry — a second copy is a fiction.

    The reader's own uniqueness rule already rejects a repeated name, so this
    is asserted where it is actually reachable: direct construction.
    """
    from agent_run_supervisor.native_acp.spec import SpecValidationError

    with pytest.raises(SpecValidationError):
        _snapshot(
            launch_permission_policy_id=lp.POLICY_DENY_WRITE_AND_SHELL_V1,
            launch_permission_digest=_GOOD_DIGEST,
            names=(
                _named(lp.CURSOR_CONFIG_DIR_ENV, lp.ENV_SOURCE_LAUNCH_PERMISSION, 5),
                _named(lp.CURSOR_CONFIG_DIR_ENV, "overlay", 3),
            ),
        )


def test_no_other_name_may_ride_the_launch_permission_layer() -> None:
    """The layer is as exclusive as the key: only the policy's pair fills it."""
    from agent_run_supervisor.native_acp.spec import (
        SpecValidationError,
        launch_payload_shape_is_exact,
    )

    # Absent pair, a stranger on the layer.
    with pytest.raises(SpecValidationError):
        _snapshot(names=(_named("SOMETHING_ELSE", lp.ENV_SOURCE_LAUNCH_PERMISSION, 5),))
    assert not launch_payload_shape_is_exact(
        _payload_with_names(
            (_named("SOMETHING_ELSE", lp.ENV_SOURCE_LAUNCH_PERMISSION, 5),)
        )
    )
    # Present pair, plus a stranger on the layer.
    with pytest.raises(SpecValidationError):
        _snapshot(
            launch_permission_policy_id=lp.POLICY_DENY_WRITE_AND_SHELL_V1,
            launch_permission_digest=_GOOD_DIGEST,
            names=(
                _named(lp.CURSOR_CONFIG_DIR_ENV, lp.ENV_SOURCE_LAUNCH_PERMISSION, 5),
                _named("ZZ_EXTRA", lp.ENV_SOURCE_LAUNCH_PERMISSION, 5),
            ),
        )


def test_the_assembler_refuses_a_relabelled_reserved_name(tmp_path: Path) -> None:
    """Constructor, reader and assembler share one predicate, so all three refuse.

    A profile that selects **no** policy is free to list the reserved key in its
    base allowlist — the profile invariant only guards a selecting one — so this
    is the reachable way to project that name under layer 1. Sealing it would
    record an operator-supplied value for a key only source may supply.
    """
    from agent_run_supervisor.native_acp.spec import SpecValidationError

    profile = _model_only_profile(
        base_allowlist=("PATH", "HOME", lp.CURSOR_CONFIG_DIR_ENV)
    )
    assembler, instance, entry, resolve, env = _assembler(tmp_path, profile)
    environment = resolve(
        arsd_env={**env, lp.CURSOR_CONFIG_DIR_ENV: "/tmp/not-ours"},
        profile=instance.profile,
        entry=entry,
    )

    with pytest.raises(SpecValidationError):
        assembler.resolve_launch(environment=environment, launch_permission=None)


# -- A: the pair predicate is total over anything ---------------------------


class _HostileId(str):
    """A ``str`` subclass is not a ``str``: it can lie about equality."""

    def __eq__(self, other):  # pragma: no cover - defensive
        return True

    def __hash__(self):
        return hash(lp.POLICY_DENY_WRITE_AND_SHELL_V1)


@pytest.mark.parametrize(
    "policy_id",
    [
        [lp.POLICY_DENY_WRITE_AND_SHELL_V1],
        {lp.POLICY_DENY_WRITE_AND_SHELL_V1},
        {"id": lp.POLICY_DENY_WRITE_AND_SHELL_V1},
        17,
        b"deny-write-and-shell-v1",
    ],
)
def test_the_pair_predicate_answers_rather_than_raising(policy_id) -> None:
    """A predicate that raises is not a predicate.

    An unhashable id reached ``in`` a frozenset and raised ``TypeError`` out of
    a public reader, which turns a refusal into a crash at whatever called it.
    """
    assert lp.policy_pair_is_exact(policy_id, _GOOD_DIGEST) is False


def test_a_string_subclass_is_not_accepted_as_a_policy_id_or_digest() -> None:
    """``type(x) is str``, not ``isinstance`` — the project's own rule."""
    assert lp.policy_pair_is_exact(_HostileId("anything"), _GOOD_DIGEST) is False
    assert (
        lp.policy_pair_is_exact(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, _HostileId(_GOOD_DIGEST)
        )
        is False
    )


def test_a_hostile_policy_id_refuses_rather_than_crashing_the_seams() -> None:
    from agent_run_supervisor.native_acp.spec import (
        SpecValidationError,
        launch_payload_shape_is_exact,
    )

    with pytest.raises(SpecValidationError):
        _snapshot(
            launch_permission_policy_id=[lp.POLICY_DENY_WRITE_AND_SHELL_V1],
            launch_permission_digest=_GOOD_DIGEST,
            names=(_policy_env_name(),),
        )
    payload = _payload_with_names(
        (_policy_env_name(),),
        launch_permission_policy_id=[lp.POLICY_DENY_WRITE_AND_SHELL_V1],
        launch_permission_digest=_GOOD_DIGEST,
    )
    assert launch_payload_shape_is_exact(payload) is False


# -- B: a partial materialization that cannot be cleaned is classified ------


def _fail_write_and_rmdir(monkeypatch, directory: Path) -> None:
    """Create the directory, fail the write, then fail the removal.

    The narrow window this reproduces: material this invocation *did* create
    survives, while the caller is told only that materialization failed — so
    nothing downstream ever learns a leftover exists.
    """
    payload = lp.compile_policy_document(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",)
    ).encode("utf-8")
    real_write = os.write
    real_rmdir = os.rmdir

    def fake_write(fd, data):
        if bytes(data) == payload:
            return 0
        return real_write(fd, data)

    def fake_rmdir(path, *args, **kwargs):
        if str(path) == str(directory):
            raise OSError(13, "Permission denied", str(directory))
        return real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "write", fake_write)
    monkeypatch.setattr(os, "rmdir", fake_rmdir)


def test_unremovable_partial_material_raises_the_cleanup_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _material_dir(tmp_path)
    _fail_write_and_rmdir(monkeypatch, directory)

    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.materialize(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
        )

    # The leftover is real, so the code has to say so: reporting only
    # "materialize failed" loses the one fact an operator needs.
    assert excinfo.value.code == lp.LAUNCH_PERMISSION_CLEANUP_FAILED
    assert str(excinfo.value) == lp.LAUNCH_PERMISSION_CLEANUP_FAILED
    assert directory.exists()
    assert str(tmp_path) not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_a_removable_partial_still_reports_materialize_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only an *unremovable* leftover changes the code."""
    payload = lp.compile_policy_document(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",)
    ).encode("utf-8")
    _partial_write(monkeypatch, payload, first=0, then=None)

    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.materialize(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
        )

    assert excinfo.value.code == lp.LAUNCH_PERMISSION_MATERIALIZE_FAILED
    assert not _material_dir(tmp_path).exists()


def test_a_refused_pre_existing_target_never_reports_a_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing was created, so there is nothing to have failed to clean."""
    directory = _material_dir(tmp_path)
    directory.mkdir()
    (directory / "planted.txt").write_text("PLANTED", encoding="utf-8")
    real_rmdir = os.rmdir

    def fake_rmdir(path, *args, **kwargs):
        if str(path) == str(directory):
            raise AssertionError("a refusal must not try to remove what it found")
        return real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "rmdir", fake_rmdir)

    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.materialize(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
        )

    assert excinfo.value.code == lp.LAUNCH_PERMISSION_MATERIALIZE_FAILED
    assert (directory / "planted.txt").read_text(encoding="utf-8") == "PLANTED"


def test_a_run_classifies_an_unremovable_partial_before_any_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-dispatch terminal is what makes the leftover durable."""
    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))
    # Built before the patch: the session seeder materializes too, and it is
    # not the subject here.
    task = harness.task(request=_cursor_request())
    directory = _material_dir(harness.run_dir())
    _fail_write_and_rmdir(monkeypatch, directory)

    result = _run(task)

    assert result.status is AgentRunStatus.FAILED
    run_dir = harness.run_dir()
    payload = json.loads((run_dir / "result.json").read_text())
    assert payload["detail_code"] == lp.LAUNCH_PERMISSION_CLEANUP_FAILED
    assert not (run_dir / "prompt-dispatch-started").exists()
    assert harness.methods_seen() == []
    # The leftover really is still there, and it is classified.
    assert directory.exists()
    _assert_categorical_marker(run_dir)
    # Nothing raw travelled with it. ``run_dir`` is a field every Native
    # terminal carries and is not this feature's to remove; what must never
    # appear is the material's own location, the errno text, or the document.
    rendered = json.dumps(payload)
    assert str(directory) not in rendered
    assert lp.LAUNCH_PERMISSION_DIRNAME not in rendered
    assert "Permission denied" not in rendered
    assert "Write(**)" not in rendered
    assert payload["failure_reason"] == "run failed"


# ===========================================================================
# Final batch — registry ownership, canonical digest, typed seams
# ===========================================================================


def _registry_text(profile: str, **declarations) -> str:
    body = [
        "schema_version = 1",
        "",
        "[agents.some-agent]",
        f'profile = "{profile}"',
        'command = "some-agent"',
    ]
    for key, value in declarations.items():
        body.append(f"{key} = {value}")
    return "\n".join(body) + "\n"


# -- 1. the registry refuses a declaration the selected policy owns ---------


def _selecting_registry(monkeypatch) -> None:
    """Put a *selecting* profile behind the id the parser resolves.

    No registered profile selects a policy any more, so the parser's
    per-selection rule would otherwise have no positive case left to exercise.
    The rule is what is under test here, not which profile happens to select —
    which is exactly the split the check itself is written to preserve.
    """
    from agent_run_supervisor.native_acp import agent_registry

    monkeypatch.setattr(
        agent_registry,
        "DEFAULT_REGISTRY",
        ProfileRegistry((_policy_profile(profile_id="cursor-native-acp-v1"),)),
    )


@pytest.mark.parametrize(
    "declaration",
    [
        {"env_passthrough": f'["{lp.CURSOR_CONFIG_DIR_ENV}"]'},
        {"env_overlay": f'{{ {lp.CURSOR_CONFIG_DIR_ENV} = "/tmp/operator-owned" }}'},
    ],
    ids=["env_passthrough", "env_overlay"],
)
def test_a_declaration_the_selected_policy_owns_refuses_the_registry(
    declaration, monkeypatch
) -> None:
    """Layer 5 wins silently, which is exactly why the parser must not stay quiet.

    An operator who writes this key has decided something. Letting the file
    parse and then overwriting the declaration at layer 5 produces a projection
    that looks consistent while hiding the conflict — so the refusal happens
    where the operator can still see it, at authoring time.
    """
    from agent_run_supervisor.native_acp.agent_registration import RegistryRefusal
    from agent_run_supervisor.native_acp.agent_registry import parse_registry_text

    _selecting_registry(monkeypatch)
    text = _registry_text("cursor-native-acp-v1", **declaration)

    with pytest.raises(RegistryRefusal) as excinfo:
        parse_registry_text(text)

    assert excinfo.value.rule == "LAUNCH_PERMISSION_KEY_COLLISION"
    message = excinfo.value.message
    assert lp.CURSOR_CONFIG_DIR_ENV in message
    # A name and a field path, never a value.
    assert "/tmp/operator-owned" not in message


def test_an_unrelated_declaration_on_a_selecting_profile_still_parses(
    monkeypatch,
) -> None:
    from agent_run_supervisor.native_acp.agent_registry import parse_registry_text

    _selecting_registry(monkeypatch)
    snapshot = parse_registry_text(
        _registry_text("cursor-native-acp-v1", env_passthrough='["SOME_OTHER_NAME"]')
    )
    assert snapshot.ids() == ("some-agent",)


def test_a_non_selecting_profile_is_not_charged_for_a_policy_it_never_uses() -> None:
    """The reservation follows the **selection**, not the key's mere existence.

    A profile that selects no launch policy materializes nothing and projects no
    layer 5, so the registry has nothing to protect here and says so.
    """
    from agent_run_supervisor.native_acp.agent_registry import parse_registry_text

    snapshot = parse_registry_text(
        _registry_text(
            "standard-native-acp-v1",
            env_passthrough=f'["{lp.CURSOR_CONFIG_DIR_ENV}"]',
        )
    )
    assert snapshot.ids() == ("some-agent",)


def test_the_reserved_key_lookup_is_driven_by_the_policy_not_an_agent_name() -> None:
    """No ``agent_id ==`` anywhere: the profile's selection is the only input."""
    assert lp.reserved_keys_for_policy(None) == frozenset()
    assert lp.reserved_keys_for_policy("write-everything-v9") == frozenset()
    assert lp.reserved_keys_for_policy(lp.POLICY_DENY_WRITE_AND_SHELL_V1) == frozenset(
        {lp.CURSOR_CONFIG_DIR_ENV}
    )
    assert lp.reserved_keys_for_policy([lp.POLICY_DENY_WRITE_AND_SHELL_V1]) == frozenset()


# -- 2. the digest must be the canonical policy's digest, not merely shaped -


def test_the_canonical_digest_is_one_table_every_seam_shares(tmp_path: Path) -> None:
    canonical = lp.canonical_policy_digest(lp.POLICY_DENY_WRITE_AND_SHELL_V1)
    document = lp.compile_policy_document(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",)
    )
    assert canonical == lp.policy_digest(document)
    material = lp.materialize(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
    )
    assert material.digest == canonical
    # Capability validation gates the compile; it never edits the bytes.
    assert lp.compile_policy_document(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read", "search")
    ) == document


def test_a_well_shaped_but_wrong_digest_is_not_exact() -> None:
    assert lp.policy_pair_is_exact(lp.POLICY_DENY_WRITE_AND_SHELL_V1, _GOOD_DIGEST)
    assert not lp.policy_pair_is_exact(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, _WRONG_DIGEST
    )


def test_a_wrong_digest_is_refused_at_construction() -> None:
    from agent_run_supervisor.native_acp.spec import SpecValidationError

    with pytest.raises(SpecValidationError):
        _snapshot(
            launch_permission_policy_id=lp.POLICY_DENY_WRITE_AND_SHELL_V1,
            launch_permission_digest=_WRONG_DIGEST,
            names=(_policy_env_name(),),
        )


def test_a_wrong_digest_is_refused_by_the_reader() -> None:
    from agent_run_supervisor.native_acp.spec import launch_payload_shape_is_exact

    payload = _payload_with_names(
        (_policy_env_name(),),
        launch_permission_policy_id=lp.POLICY_DENY_WRITE_AND_SHELL_V1,
        launch_permission_digest=_WRONG_DIGEST,
    )
    assert launch_payload_shape_is_exact(payload) is False


def test_a_wrong_digest_is_refused_by_the_assembler(tmp_path: Path) -> None:
    import dataclasses

    from agent_run_supervisor.native_acp.spec import SpecValidationError

    assembler, instance, entry, resolve, env = _assembler(tmp_path, _policy_profile())
    material = lp.materialize(
        lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
    )
    tampered = dataclasses.replace(material, digest=_WRONG_DIGEST)
    environment = resolve(
        arsd_env=env,
        profile=instance.profile,
        entry=entry,
        launch_permission=material.env_pairs,
    )

    with pytest.raises(SpecValidationError):
        assembler.resolve_launch(environment=environment, launch_permission=tampered)


# -- 3. public seams answer categorically instead of throwing raw errors ----


class _HostileValue:
    """Unhashable, and its ``repr`` is a leak marker."""

    def __repr__(self) -> str:
        return "HOSTILE-REPR-LEAK"

    def __hash__(self):
        raise RuntimeError("HOSTILE-HASH-LEAK")

    def __eq__(self, other):  # pragma: no cover - defensive
        return True


@pytest.mark.parametrize(
    "policy_id",
    [
        [lp.POLICY_DENY_WRITE_AND_SHELL_V1],
        {"id": lp.POLICY_DENY_WRITE_AND_SHELL_V1},
        _HostileId("anything"),
        _HostileValue(),
        17,
    ],
)
def test_profile_construction_refuses_a_non_string_policy_id(policy_id) -> None:
    """A public constructor must refuse, not crash — and refuse without looking.

    Membership before type judgement raised ``TypeError``/``RuntimeError`` out
    of profile construction, and formatting the offender with ``!r`` invited its
    own ``__repr__`` into the message.
    """
    with pytest.raises(ProfileValidationError) as excinfo:
        _model_only_profile(launch_permission_policy_id=policy_id)
    text = str(excinfo.value)
    assert "HOSTILE-REPR-LEAK" not in text
    assert "HOSTILE-HASH-LEAK" not in text


def test_profile_construction_still_names_a_merely_unregistered_policy() -> None:
    """A plain string that is simply not registered keeps its legible refusal."""
    with pytest.raises(ProfileValidationError) as excinfo:
        _model_only_profile(launch_permission_policy_id="write-everything-v9")
    assert "write-everything-v9" in str(excinfo.value)


@pytest.mark.parametrize(
    "carrier",
    [object(), _HostileValue(), {"policy_id": "x", "digest": "y"}, "material", 17],
)
def test_the_assembler_accepts_only_the_material_carrier(
    tmp_path: Path, carrier
) -> None:
    """``.policy_id`` on an arbitrary object is an ``AttributeError``, not a refusal."""
    from agent_run_supervisor.native_acp.spec import SpecValidationError

    assembler, instance, entry, resolve, env = _assembler(tmp_path, _policy_profile())
    environment = resolve(arsd_env=env, profile=instance.profile, entry=entry)

    with pytest.raises(SpecValidationError) as excinfo:
        assembler.resolve_launch(environment=environment, launch_permission=carrier)
    assert "HOSTILE-REPR-LEAK" not in str(excinfo.value)
    assert "HOSTILE-HASH-LEAK" not in str(excinfo.value)


# ===========================================================================
# Final narrow repair — the directory descriptor's close is part of the work
# ===========================================================================


def _fail_close_of(monkeypatch, directory: Path, *, which: str):
    """Fail exactly one descriptor's close, identified by how it was opened.

    Deterministic rather than indiscriminate: the directory descriptor is the
    one opened on ``directory`` with ``O_DIRECTORY``, and the config handle is
    the one opened *relative* to it via ``dir_fd=``. Nothing else in the
    process is touched, and the descriptor is really closed before the failure
    is raised, so the test leaks nothing.
    """
    real_open = os.open
    real_close = os.close
    doomed: set[int] = set()

    def fake_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        path = args[0] if args else kwargs.get("path")
        flags = args[1] if len(args) > 1 else kwargs.get("flags", 0)
        is_dir_fd = bool(flags & os.O_DIRECTORY) and str(path) == str(directory)
        is_handle = kwargs.get("dir_fd") is not None or len(args) > 3
        if (which == "directory" and is_dir_fd) or (which == "handle" and is_handle):
            doomed.add(fd)
        return fd

    def fake_close(fd):
        if fd in doomed:
            doomed.discard(fd)
            real_close(fd)
            raise OSError(9, "Bad file descriptor")
        return real_close(fd)

    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "close", fake_close)


def _fail_rmdir_of(monkeypatch, directory: Path) -> None:
    real_rmdir = os.rmdir

    def fake_rmdir(path, *args, **kwargs):
        if str(path) == str(directory):
            raise OSError(13, "Permission denied", str(directory))
        return real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "rmdir", fake_rmdir)


def test_a_directory_close_failure_rolls_back_and_reports_materialize_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing the directory descriptor is work, not cleanup after work.

    It sat in the outer ``finally``, past the ``except OSError`` suite, so a
    close failure escaped as a raw ``OSError`` — skipping rollback entirely and
    leaving a directory no later step could classify, because no material
    carrier was ever returned for the Run to clean up.
    """
    directory = _material_dir(tmp_path)
    _fail_close_of(monkeypatch, directory, which="directory")

    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.materialize(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
        )

    assert excinfo.value.code == lp.LAUNCH_PERMISSION_MATERIALIZE_FAILED
    assert not directory.exists()
    assert str(tmp_path) not in str(excinfo.value)
    assert "Bad file descriptor" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_a_directory_close_failure_whose_rollback_also_fails_is_cleanup_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _material_dir(tmp_path)
    _fail_close_of(monkeypatch, directory, which="directory")
    _fail_rmdir_of(monkeypatch, directory)

    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.materialize(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
        )

    assert excinfo.value.code == lp.LAUNCH_PERMISSION_CLEANUP_FAILED
    assert directory.exists()
    assert "Permission denied" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


def test_a_handle_close_failure_keeps_its_own_already_correct_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distinct coverage: the config handle closes inside the guarded body.

    Its failure already reaches ``except OSError``; this pins that the two
    descriptors are handled separately rather than by one blanket close patch.
    """
    directory = _material_dir(tmp_path)
    _fail_close_of(monkeypatch, directory, which="handle")

    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.materialize(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
        )

    assert excinfo.value.code == lp.LAUNCH_PERMISSION_MATERIALIZE_FAILED
    assert not directory.exists()


def test_a_directory_close_failure_never_touches_a_pre_existing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing target semantics are untouched: mkdir refuses first."""
    directory = _material_dir(tmp_path)
    directory.mkdir()
    (directory / "planted.txt").write_text("PLANTED", encoding="utf-8")
    _fail_close_of(monkeypatch, directory, which="directory")

    with pytest.raises(lp.LaunchPermissionError) as excinfo:
        lp.materialize(
            lp.POLICY_DENY_WRITE_AND_SHELL_V1, capabilities=("read",), run_dir=tmp_path
        )

    assert excinfo.value.code == lp.LAUNCH_PERMISSION_MATERIALIZE_FAILED
    assert (directory / "planted.txt").read_text(encoding="utf-8") == "PLANTED"


def test_a_run_classifies_a_directory_close_failure_before_any_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch, dict(CURSOR_SCRIPT))
    # Built before the patch: the session seeder materializes too, and it is
    # not the subject here.
    task = harness.task(request=_cursor_request())
    directory = _material_dir(harness.run_dir())
    _fail_close_of(monkeypatch, directory, which="directory")
    _fail_rmdir_of(monkeypatch, directory)

    result = _run(task)

    assert result.status is AgentRunStatus.FAILED
    run_dir = harness.run_dir()
    payload = json.loads((run_dir / "result.json").read_text())
    assert payload["detail_code"] == lp.LAUNCH_PERMISSION_CLEANUP_FAILED
    assert not (run_dir / "prompt-dispatch-started").exists()
    assert harness.methods_seen() == []
    assert directory.exists()
    _assert_categorical_marker(run_dir)
    rendered = json.dumps(payload)
    assert str(directory) not in rendered
    assert lp.LAUNCH_PERMISSION_DIRNAME not in rendered
    assert "Bad file descriptor" not in rendered
    assert "Permission denied" not in rendered

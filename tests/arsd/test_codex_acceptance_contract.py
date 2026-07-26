"""Hermetic contract tests for the opt-in Codex socket acceptance harness.

The acceptance module is skip-by-default and needs real credentials, the real
adapter/CLI pair, and a controller-authorized boundary. Its *assertion logic*
must not be: a gate whose conditions are tautological, whose cleanup runs only
on the happy path, or whose case matrix silently drops a required variant would
pass while proving nothing.

This suite loads that module and drives its pure helpers against **synthetic
fixtures only** — no daemon, no spawn, no model call, no real credential root,
no ``ARS_CODEX_SOCKET_ACCEPTANCE`` opt-in, and no reference to the production
``CODEX_HOME``. Every credential-shaped byte here is a placeholder created by
the test itself.
"""

from __future__ import annotations

import errno
import hashlib
import importlib.util
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_run_supervisor.exit_classifier import _RETRYABLE_DEFAULT, AgentRunStatus
from agent_run_supervisor.native_acp import attestation as attestation_module
from agent_run_supervisor.native_acp.run_task import (
    FinalizationObservations,
    finalize_run_state,
)
from agent_run_supervisor.native_acp.spec import RunLimits

_ACCEPTANCE_PATH = (
    Path(__file__).resolve().parent / "test_codex_socket_acceptance.py"
)


def _load_acceptance():
    """Import the opt-in module for inspection without collecting it twice.

    Loaded by path under its own name, so pytest's own import of the module
    (which the default skip then empties) is untouched. Registering it in
    ``sys.modules`` first is required: ``dataclasses`` resolves a class's
    module to interpret its annotations.
    """
    name = "codex_socket_acceptance_contract_view"
    spec = importlib.util.spec_from_file_location(name, _ACCEPTANCE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


acceptance = _load_acceptance()

SYNTHETIC_AUTH = acceptance.SYNTHETIC_AUTH_BYTES


# --- synthetic stand-ins -----------------------------------------------------


def _synthetic_credential_root(tmp_path: Path) -> tuple[Path, Path]:
    """A private 0700 root with placeholder bytes — never the real CODEX_HOME."""
    root = tmp_path / "synthetic-real-root"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    auth = root / "auth.json"
    auth.write_bytes(SYNTHETIC_AUTH)
    os.chmod(auth, 0o600)
    return root, auth


def _synthetic_fixtures(tmp_path: Path) -> SimpleNamespace:
    """The attribute surface ``_arrange_negative_case`` mutates, nothing more.

    Deliberately not ``PrivateNegativeFixtures``: that builder copies the real
    frozen Node out of the registered profile, which no hermetic test may
    depend on.
    """
    base = tmp_path / "neg"
    stage = base / "stage"
    stage.mkdir(parents=True)
    node = stage / "node"
    node.write_bytes(b"#!/bin/false\n# synthetic node copy\n")
    entry = stage / "codex-fake-agent.mjs"
    entry.write_text("// synthetic codex-shaped fake agent\n", encoding="utf-8")
    cli_target = stage / "codex-placeholder"
    cli_target.write_bytes(b"#!/bin/false\n# synthetic placeholder cli\n")
    cli = stage / "codex"
    cli.symlink_to(cli_target)
    cred_root = base / "synthetic-codex-home"
    cred_root.mkdir(mode=0o700)
    os.chmod(cred_root, 0o700)
    auth = cred_root / "auth.json"
    auth.write_bytes(SYNTHETIC_AUTH)
    os.chmod(auth, 0o600)
    workspace = base / "neg-workspace"
    workspace.mkdir()
    nested_cwd = workspace / "nested"
    nested_cwd.mkdir()
    return SimpleNamespace(
        base=base,
        node=node,
        entry=entry,
        cli=cli,
        cli_target=cli_target,
        cred_root=cred_root,
        auth=auth,
        workspace=workspace,
        nested_cwd=nested_cwd,
    )


def _fingerprint(path: Path) -> str:
    try:
        info = os.lstat(path)
    except OSError:
        return "absent"
    if stat.S_ISLNK(info.st_mode):
        return f"symlink:{os.readlink(path)}"
    if stat.S_ISDIR(info.st_mode):
        return f"dir:{oct(stat.S_IMODE(info.st_mode))}"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"file:{oct(stat.S_IMODE(info.st_mode))}:{digest}"


def _surfaces(fixtures: SimpleNamespace) -> dict[str, str]:
    layer = Path(".codex") / "config.toml"
    return {
        "node": _fingerprint(fixtures.node),
        "entry": _fingerprint(fixtures.entry),
        "cli": _fingerprint(fixtures.cli),
        "cred_root": _fingerprint(fixtures.cred_root),
        "auth": _fingerprint(fixtures.auth),
        "root_config_toml": _fingerprint(fixtures.cred_root / "config.toml"),
        "layer_at_cwd": _fingerprint(fixtures.nested_cwd / layer),
        "layer_at_root": _fingerprint(fixtures.workspace / layer),
        "layer_above_root": _fingerprint(fixtures.workspace.parent / layer),
    }


# --- B2: credential staging always cleans and always re-inventories ----------


def _inode_targeted(real, target: Path, *, close_first: bool = False):
    """Fail exactly one syscall — the first one touching the staged inode.

    One-shot on purpose: a transient staging fault must not also disable the
    cleanup that has to run afterwards, or the test would prove the injection
    rather than the guard.
    """
    fired = False

    def fake(fd, *args, **kwargs):
        nonlocal fired
        if fired:
            return real(fd, *args, **kwargs)
        try:
            info = os.fstat(fd)
            wanted = target.stat()
        except OSError:
            return real(fd, *args, **kwargs)
        if (info.st_dev, info.st_ino) == (wanted.st_dev, wanted.st_ino):
            fired = True
            if close_first:
                real(fd)
            raise OSError(errno.EIO, "injected staging failure")
        return real(fd, *args, **kwargs)

    return fake


@pytest.mark.parametrize("syscall", ["write", "fsync", "close"])
def test_staging_failure_cleans_and_reinventories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, syscall: str
) -> None:
    """A failure after the staged file exists must still clean *and* verify.

    The staging call therefore has to sit inside the guarded region: leaving it
    outside means a write/fsync/close failure keeps the copied credential bytes
    on disk and skips the real-root identity check entirely.
    """
    real_root, real_auth = _synthetic_credential_root(tmp_path)
    home = tmp_path / "positive-codex-home"
    staged = home / "auth.json"
    inventories: list[str] = []
    real_inventory = acceptance._inventory_tree

    def counting_inventory(root: Path):
        inventories.append(str(root))
        return real_inventory(root)

    monkeypatch.setattr(acceptance, "_inventory_tree", counting_inventory)
    monkeypatch.setattr(
        os,
        syscall,
        _inode_targeted(getattr(os, syscall), staged, close_first=syscall == "close"),
    )

    with pytest.raises(OSError) as err:
        with acceptance._credential_isolation(home, real_auth, real_root):
            pytest.fail("the leg body must never run after a staging failure")
    monkeypatch.undo()

    assert err.value.errno == errno.EIO
    assert not staged.exists(), "staged credential copy survived a staging failure"
    assert not home.exists(), "ephemeral positive home survived a staging failure"
    # Pre-inventory and post-inventory both ran on the failure path.
    assert inventories == [str(real_root), str(real_root)]


def test_leg_failure_still_cleans_and_reinventories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_root, real_auth = _synthetic_credential_root(tmp_path)
    home = tmp_path / "positive-codex-home"
    inventories: list[str] = []
    real_inventory = acceptance._inventory_tree

    def counting_inventory(root: Path):
        inventories.append(str(root))
        return real_inventory(root)

    monkeypatch.setattr(acceptance, "_inventory_tree", counting_inventory)

    with pytest.raises(RuntimeError, match="leg exploded"):
        with acceptance._credential_isolation(home, real_auth, real_root):
            assert (home / "auth.json").exists()
            raise RuntimeError("leg exploded")

    assert not (home / "auth.json").exists()
    assert not home.exists()
    assert inventories == [str(real_root), str(real_root)]


def test_clean_leg_stages_reads_and_removes_the_ephemeral_copy(
    tmp_path: Path,
) -> None:
    real_root, real_auth = _synthetic_credential_root(tmp_path)
    home = tmp_path / "positive-codex-home"

    with acceptance._credential_isolation(home, real_auth, real_root):
        staged = home / "auth.json"
        assert staged.read_bytes() == SYNTHETIC_AUTH
        assert stat.S_IMODE(staged.stat().st_mode) == 0o600
        assert stat.S_IMODE(home.stat().st_mode) == 0o700

    assert not home.exists()


def test_cleanup_residue_is_reported_not_raised(tmp_path: Path) -> None:
    home = tmp_path / "positive-codex-home"
    home.mkdir(mode=0o700)
    (home / "auth.json").write_bytes(SYNTHETIC_AUTH)
    os.chmod(home / "auth.json", 0o600)

    assert acceptance._teardown_credentials(home) == ()
    assert not home.exists()
    # A second teardown of an already-absent home is still clean.
    assert acceptance._teardown_credentials(home) == ()


def test_surviving_staged_copy_raises_isolation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_root, real_auth = _synthetic_credential_root(tmp_path)
    home = tmp_path / "positive-codex-home"
    monkeypatch.setattr(
        acceptance, "_teardown_credentials", lambda _home: ("staged_credential_copy_survived",)
    )

    with pytest.raises(acceptance.CredentialIsolationError, match="cleanup residue"):
        with acceptance._credential_isolation(home, real_auth, real_root):
            pass
    monkeypatch.undo()
    acceptance._teardown_credentials(home)


def test_real_root_drift_fails_even_when_the_leg_passed(
    tmp_path: Path,
) -> None:
    real_root, real_auth = _synthetic_credential_root(tmp_path)
    home = tmp_path / "positive-codex-home"

    with pytest.raises(acceptance.CredentialIsolationError, match="real-root drift"):
        with acceptance._credential_isolation(home, real_auth, real_root):
            # Any drift in the observed root — here an added entry — fails the
            # whole leg, even though the leg body itself succeeded.
            (real_root / "unexpected-entry").write_text("x", encoding="utf-8")
    assert not home.exists()


def test_original_leg_exception_survives_as_the_isolation_error_context(
    tmp_path: Path,
) -> None:
    real_root, real_auth = _synthetic_credential_root(tmp_path)
    home = tmp_path / "positive-codex-home"

    with pytest.raises(acceptance.CredentialIsolationError) as err:
        with acceptance._credential_isolation(home, real_auth, real_root):
            (real_root / "unexpected-entry").write_text("x", encoding="utf-8")
            raise RuntimeError("the leg failed first")
    assert isinstance(err.value.__context__, RuntimeError)
    assert "the leg failed first" in str(err.value.__context__)


@pytest.mark.parametrize("field", ["st_atime_ns", "st_mtime_ns", "st_ctime_ns"])
def test_inventory_drift_names_the_field_but_never_the_entry(field: str) -> None:
    pre = {"auth.json": {"st_atime_ns": 1, "st_mtime_ns": 2, "st_ctime_ns": 3}}
    post = {"auth.json": {**pre["auth.json"], field: 99}}

    drift = acceptance._inventory_drift(pre, post)
    assert field in drift
    assert "entries:1" in drift
    assert not any("auth.json" in item for item in drift)
    assert acceptance._inventory_drift(pre, pre) == ()


def test_inventory_drift_detects_an_added_or_removed_entry() -> None:
    pre = {"a": {"st_ino": 1}}
    assert acceptance._inventory_drift(pre, {**pre, "b": {"st_ino": 2}})[0] == "entry_set"
    assert acceptance._inventory_drift(pre, {})[0] == "entry_set"


# --- B3: P2 continuity, current-turn separation, thread-state delta ----------


def test_context_nonce_is_deterministic_and_non_secret() -> None:
    first = acceptance._context_nonce("p2_continuity_and_b1_boundary", "abc123def456")
    again = acceptance._context_nonce("p2_continuity_and_b1_boundary", "abc123def456")
    other = acceptance._context_nonce("p2_continuity_and_b1_boundary", "999999999999")

    assert first == again  # deterministic: a rerun asks for the same token
    assert first != other  # bound to the gate target
    assert first.startswith("ARS-CONTINUITY-")
    # Derived only from public inputs, so it can never carry credential entropy.
    assert SYNTHETIC_AUTH.decode() not in first
    assert set(first) <= set("ABCDEF0123456789-ARSCONTINUITY")


@pytest.mark.parametrize(
    "message",
    [
        "",
        "I do not recall any token.",
        "the token was NONCE-TOKEN-1 I think",
        "NONCE-TOKEN-1 NONCE-TOKEN-1",
        "Run 1 said NONCE-TOKEN-1. NONCE-TOKEN-1",
        "nonce-token-1",
    ],
)
def test_exact_nonce_recall_rejects_everything_but_the_token(message: str) -> None:
    with pytest.raises(AssertionError):
        acceptance._assert_exact_nonce_recall(message, "NONCE-TOKEN-1")


@pytest.mark.parametrize("message", ["NONCE-TOKEN-1", "NONCE-TOKEN-1\n", "  NONCE-TOKEN-1  "])
def test_exact_nonce_recall_accepts_only_the_bare_token(message: str) -> None:
    acceptance._assert_exact_nonce_recall(message, "NONCE-TOKEN-1")


def test_exact_nonce_recall_refuses_an_empty_nonce() -> None:
    with pytest.raises(AssertionError, match="non-empty"):
        acceptance._assert_exact_nonce_recall("anything", "")


def test_home_state_excludes_the_staged_credential_copy(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "sessions" / "2026").mkdir(parents=True)
    (home / "auth.json").write_bytes(SYNTHETIC_AUTH)
    (home / "sessions" / "2026" / "rollout.jsonl").write_text("x", encoding="utf-8")

    state = acceptance._home_state(home)
    assert "auth.json" not in state
    assert "sessions/2026/rollout.jsonl" in state


def test_new_thread_state_requires_a_real_delta(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "auth.json").write_bytes(SYNTHETIC_AUTH)

    before = acceptance._home_state(home)
    # The staged credential copy alone must not count as "the CLI persisted
    # thread state": an `any(home.iterdir())` form is already true here.
    assert any(home.iterdir())
    with pytest.raises(AssertionError, match="no new CLI thread/rollout state"):
        acceptance._assert_new_thread_state(before, acceptance._home_state(home))

    (home / "history.jsonl").write_text("x", encoding="utf-8")
    assert acceptance._assert_new_thread_state(before, acceptance._home_state(home)) == 1


def _events(*, replay: tuple[int, ...] = (), current: tuple[int, ...] = ()) -> list[dict]:
    events: list[dict] = [{"type": "session_load_requested"}]
    events += [{"type": "agent_message_delta", "text_length": n} for n in replay]
    events.append({"type": "session_prompt_sent"})
    events += [{"type": "agent_message_delta", "text_length": n} for n in current]
    return events


def test_current_turn_message_path_measures_only_post_prompt_deltas() -> None:
    events = _events(replay=(40, 10), current=(3, 4))

    facts = acceptance._assert_current_turn_message_path(events, "1234567")
    assert facts == {
        "current_turn_message_length": 7,
        "replayed_message_length": 50,
    }


def test_current_turn_message_path_rejects_replay_concatenation() -> None:
    events = _events(replay=(50,), current=(7,))
    # A final message that swallowed the replayed history is exactly the B1
    # regression this measurement exists to catch.
    with pytest.raises(AssertionError, match="replay or history leaked"):
        acceptance._assert_current_turn_message_path(events, "x" * 57)


def test_current_turn_message_path_requires_a_current_turn_message() -> None:
    with pytest.raises(AssertionError, match="no current-Turn"):
        acceptance._assert_current_turn_message_path(_events(replay=(5,)), "")


def test_current_turn_message_path_requires_a_dispatched_prompt() -> None:
    with pytest.raises(AssertionError, match="never dispatched"):
        acceptance._assert_current_turn_message_path(
            [{"type": "agent_message_delta", "text_length": 3}], "abc"
        )


def test_recall_prompt_asks_for_the_token_without_restating_it() -> None:
    nonce = acceptance._context_nonce("p2_continuity_and_b1_boundary", "0" * 40)
    assert nonce in acceptance._nonce_prompt(nonce)
    # Run 2 must not be handed the answer it is being asked to recall.
    assert nonce not in acceptance.RECALL_PROMPT


def test_only_the_exact_result_legs_carry_the_nonce_prompt() -> None:
    nonce = acceptance._context_nonce("p1_exact_config_and_evidence", "0" * 40)
    for leg in acceptance.POSITIVE_LEGS:
        prompt = acceptance._positive_prompt(leg, nonce)
        assert (nonce in prompt) is (leg in acceptance.NONCE_LEGS), leg
    assert set(acceptance.NONCE_LEGS) < set(acceptance.POSITIVE_LEGS)
    # The P4 sublegs need Turns that cannot self-terminate before the cancel or
    # the post-dispatch timeout lands.
    for leg in (acceptance.P4_CANCEL_LEG, acceptance.P4_TIMEOUT_LEG):
        assert len(acceptance._positive_prompt(leg, nonce)) > 40, leg
    assert acceptance._positive_prompt(
        acceptance.P4_CANCEL_LEG, nonce
    ) != acceptance._positive_prompt(acceptance.P4_TIMEOUT_LEG, nonce)


# --- B4: two P4 sublegs against the B2-fixed terminal table ------------------


def test_p4_declares_both_sublegs_distinctly() -> None:
    assert acceptance.P4_CANCEL_LEG != acceptance.P4_TIMEOUT_LEG
    assert set(acceptance.P4_EXPECTED_OUTCOMES) == {
        acceptance.P4_CANCEL_LEG,
        acceptance.P4_TIMEOUT_LEG,
    }
    assert acceptance.P4_CANCEL_LEG in acceptance.POSITIVE_LEGS
    assert acceptance.P4_TIMEOUT_LEG in acceptance.POSITIVE_LEGS
    # Distinct evidence entries: the two sublegs are separate parametrized legs.
    assert len(set(acceptance.POSITIVE_LEGS)) == len(acceptance.POSITIVE_LEGS)
    # Their expectations must differ, or one leg is proving the other's row.
    cancel = acceptance.P4_EXPECTED_OUTCOMES[acceptance.P4_CANCEL_LEG]
    timeout = acceptance.P4_EXPECTED_OUTCOMES[acceptance.P4_TIMEOUT_LEG]
    assert cancel["detail_code"] != timeout["detail_code"]


@pytest.mark.parametrize(
    "leg, observations",
    [
        (
            "p4_cancel_after_dispatch",
            {"supervisor_cancelled": True},
        ),
        (
            "p4_timeout_after_dispatch",
            {"supervisor_timed_out": True},
        ),
    ],
)
def test_p4_expectations_are_the_production_terminal_table(
    leg: str, observations: dict
) -> None:
    """The matrix is derived from `finalize_run_state`, not copied opinion.

    Both sublegs end a *dispatched* Turn by supervisor force with no
    trustworthy ACP terminal, which the B2-fixed table resolves to
    unknown/quarantined/hard-non-retryable.
    """
    status, disposition = finalize_run_state(
        FinalizationObservations(
            dispatch_started=True,
            escalated_kill_after_dispatch=True,
            **observations,
        )
    )
    expected = acceptance.P4_EXPECTED_OUTCOMES[leg]

    assert status is AgentRunStatus.UNKNOWN
    assert expected["status"] == status.value
    assert expected["session_state"] == disposition
    assert expected["retryable"] is _RETRYABLE_DEFAULT[status]
    assert expected["retryable"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        {"status": "cancelled"},
        {"status": "timed_out"},
        {"status": "completed"},
        {"detail_code": "TURN_TIMEOUT"},
        {"detail_code": None},
        {"retryable": True},
    ],
)
def test_p4_outcome_assertion_rejects_any_drift(mutation: dict) -> None:
    leg = acceptance.P4_CANCEL_LEG
    result = {
        "status": "unknown",
        "detail_code": "SUPERVISOR_CANCELLED",
        "retryable": False,
    }
    acceptance._assert_p4_outcome(leg, result)  # the exact row passes

    with pytest.raises(AssertionError, match=f"P4 {leg}"):
        acceptance._assert_p4_outcome(leg, {**result, **mutation})


def test_p4_timeout_limit_bounds_only_the_post_dispatch_turn() -> None:
    limits = RunLimits(turn_timeout_seconds=acceptance.P4_TURN_TIMEOUT_SECONDS)

    assert limits.turn_timeout_seconds == acceptance.P4_TURN_TIMEOUT_SECONDS
    assert acceptance.P4_TURN_TIMEOUT_SECONDS > 0
    # Admission, spawn, and the startup/config sequence keep their default
    # budgets, so a timeout on this leg provably cannot fire before dispatch.
    assert limits.startup_timeout_seconds == RunLimits().startup_timeout_seconds
    assert acceptance.P4_TURN_TIMEOUT_SECONDS < limits.startup_timeout_seconds


def test_no_prompt_replay_rejects_a_second_dispatch(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / acceptance.DISPATCH_STARTED_MARKER).write_text(
        '{"marker": "prompt-dispatch-started", "ordinal": 1}', encoding="utf-8"
    )
    single = _events(current=(4,))

    acceptance._assert_no_prompt_replay(single, run_dir)

    replayed = single + [{"type": "session_prompt_sent"}]
    with pytest.raises(AssertionError, match="dispatched 2 times"):
        acceptance._assert_no_prompt_replay(replayed, run_dir)
    with pytest.raises(AssertionError, match="dispatched 0 times"):
        acceptance._assert_no_prompt_replay([{"type": "run_started"}], run_dir)


# --- B5: the complete N1–N9 variant matrix -----------------------------------


def test_declared_cases_cover_every_required_variant_exactly_once() -> None:
    declared = [case.case_id for case in acceptance.NEGATIVE_CASES]
    required = [
        case_id
        for variants in acceptance.REQUIRED_NEGATIVE_VARIANTS.values()
        for case_id in variants
    ]

    assert len(declared) == len(set(declared)), "duplicate negative case id"
    missing = sorted(set(required) - set(declared))
    extra = sorted(set(declared) - set(required))
    assert not missing, f"required R8 variants not driven: {missing}"
    assert not extra, f"cases declared outside the required matrix: {extra}"
    assert sorted(acceptance.REQUIRED_NEGATIVE_VARIANTS) == [
        f"n{index}" for index in range(1, 10)
    ]


def test_every_family_keeps_its_full_variant_count() -> None:
    # The failure this pins: a family silently represented by one specimen.
    by_family: dict[str, list[str]] = {}
    for case in acceptance.NEGATIVE_CASES:
        by_family.setdefault(case.family, []).append(case.case_id)

    for family, variants in acceptance.REQUIRED_NEGATIVE_VARIANTS.items():
        assert sorted(by_family.get(family, [])) == sorted(variants), family
    assert len(acceptance.NEGATIVE_CASES) == 18


def test_case_stages_and_rows_agree_with_the_attestation_classes() -> None:
    for case in acceptance.NEGATIVE_CASES:
        if case.stage == "attestation":
            assert case.failing_row, case.case_id
            # The declared detail code is the row's own refusal class, taken
            # from production rather than restated here.
            assert (
                attestation_module._CHECK_CLASSES[case.failing_row]
                == case.detail_code
            ), case.case_id
        elif case.stage == "admission":
            assert case.failing_row is None, case.case_id
            assert case.detail_code == "ADMISSION", case.case_id
        elif case.stage == "binding":
            # The single per-Run Binding read refuses before the RunTask is
            # even constructed, so the Run carries only the write-once
            # registration-failure terminal — no spec, launch, or attestation.
            assert case.failing_row is None, case.case_id
            assert case.detail_code == "REGISTRATION_FAILED", case.case_id
        else:
            # Session-binding refusals leave `_bind_session` through the
            # RunTask top-level guard, so they surface as RUN_EXCEPTION — not
            # ADMISSION. Pinned end-to-end by
            # tests/native_acp/test_run_task.py::
            # test_codex_seeded_session_profile_hash_drift_refused_before_attestation
            # and ::test_codex_quarantined_session_reuse_refused_before_attestation.
            assert case.stage == "session", case.case_id
            assert case.failing_row is None, case.case_id
            assert case.detail_code == "RUN_EXCEPTION", case.case_id


def test_only_reuse_dependent_cases_are_seeded() -> None:
    seeded = {case.case_id for case in acceptance.NEGATIVE_CASES if case.seeded}
    assert seeded == {
        "n7_project_config_inserted_between_runs",
        "n9_seeded_session_profile_hash_drift",
        "n9_quarantined_session_reuse",
    }


_EXPECTED_SURFACES: dict[str, set[str]] = {
    "n1_tampered_adapter_entry": {"entry"},
    "n2_swapped_node_binary": {"node"},
    "n3_retargeted_cli_symlink": {"cli"},
    "n4_auth_json_symlink": {"auth"},
    "n4_auth_json_mode_0644": {"auth"},
    "n4_auth_json_removed": {"auth"},
    "n5_credential_root_mode_0750": {"cred_root"},
    "n5_credential_root_symlink": {"cred_root"},
    "n6_credential_root_config_toml": {"root_config_toml"},
    "n7_project_config_at_cwd": {"layer_at_cwd"},
    "n7_project_config_at_workspace_root": {"layer_at_root"},
    "n7_project_config_above_workspace_root": {"layer_above_root"},
    "n7_project_config_inserted_between_runs": {"layer_at_root"},
    "n8_credential_refs_missing": set(),
    "n8_credential_refs_wrong": set(),
    "n8_credential_refs_extra": set(),
    "n9_seeded_session_profile_hash_drift": set(),
    "n9_quarantined_session_reuse": set(),
}


@pytest.mark.parametrize(
    "case", acceptance.NEGATIVE_CASES, ids=lambda case: case.case_id
)
def test_arrangement_helper_tampers_exactly_one_declared_surface(
    tmp_path: Path, case
) -> None:
    """Every declared variant is realized, and only its own surface moves."""
    fixtures = _synthetic_fixtures(tmp_path)
    before = _surfaces(fixtures)

    overrides = acceptance._arrange_negative_case(case.case_id, fixtures)

    after = _surfaces(fixtures)
    changed = {key for key in before if before[key] != after[key]}
    assert changed == _EXPECTED_SURFACES[case.case_id], case.case_id

    if case.family == "n8":
        refs = overrides["credential_refs"]
        assert refs != ["codex-home-auth"], "an N8 variant must not match"
    else:
        assert "credential_refs" not in overrides
    # Only the three positional N7 variants steer the effective cwd.
    if case.case_id in {
        "n7_project_config_at_cwd",
        "n7_project_config_at_workspace_root",
        "n7_project_config_above_workspace_root",
    }:
        assert overrides["cwd"] == str(fixtures.nested_cwd)
    else:
        assert "cwd" not in overrides


def test_n4_variants_realize_distinct_structural_violations(tmp_path: Path) -> None:
    symlink = _synthetic_fixtures(tmp_path / "symlink")
    acceptance._arrange_negative_case("n4_auth_json_symlink", symlink)
    assert symlink.auth.is_symlink()

    mode = _synthetic_fixtures(tmp_path / "mode")
    acceptance._arrange_negative_case("n4_auth_json_mode_0644", mode)
    assert stat.S_IMODE(mode.auth.lstat().st_mode) == 0o644

    removed = _synthetic_fixtures(tmp_path / "removed")
    acceptance._arrange_negative_case("n4_auth_json_removed", removed)
    assert not removed.auth.exists()


def test_n5_variants_realize_distinct_root_violations(tmp_path: Path) -> None:
    mode = _synthetic_fixtures(tmp_path / "mode")
    acceptance._arrange_negative_case("n5_credential_root_mode_0750", mode)
    assert stat.S_IMODE(mode.cred_root.lstat().st_mode) == 0o750
    assert not mode.cred_root.is_symlink()

    swapped = _synthetic_fixtures(tmp_path / "swapped")
    acceptance._arrange_negative_case("n5_credential_root_symlink", swapped)
    assert swapped.cred_root.is_symlink()


def test_n8_variants_are_missing_wrong_and_extra(tmp_path: Path) -> None:
    registered = ["codex-home-auth"]
    seen = {}
    for case_id in acceptance.REQUIRED_NEGATIVE_VARIANTS["n8"]:
        fixtures = _synthetic_fixtures(tmp_path / case_id)
        seen[case_id] = acceptance._arrange_negative_case(case_id, fixtures)[
            "credential_refs"
        ]

    assert seen["n8_credential_refs_missing"] == []
    assert seen["n8_credential_refs_wrong"] and (
        seen["n8_credential_refs_wrong"] != registered
    )
    assert len(seen["n8_credential_refs_extra"]) > len(registered)
    assert registered[0] in seen["n8_credential_refs_extra"]


def test_arrangement_helper_refuses_an_undeclared_case(tmp_path: Path) -> None:
    fixtures = _synthetic_fixtures(tmp_path)
    with pytest.raises(AssertionError, match="undeclared negative case"):
        acceptance._arrange_negative_case("n4_something_invented", fixtures)


# --- the opt-in module stays opt-in ------------------------------------------


def test_acceptance_module_remains_skip_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARS_CODEX_SOCKET_ACCEPTANCE", raising=False)
    assert acceptance._acceptance_ready() is False
    assert acceptance.pytestmark.args[0] is True  # the skipif condition holds

    monkeypatch.setenv("ARS_CODEX_SOCKET_ACCEPTANCE", "1")
    # The gate needs every test-scoped input as well, never the flag alone.
    for name in acceptance._REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    assert acceptance._acceptance_ready() is False


def test_every_declared_leg_is_parametrized_by_the_module() -> None:
    positive = next(
        mark
        for mark in acceptance.test_positive_legs.pytestmark
        if mark.name == "parametrize"
    )
    negative = next(
        mark
        for mark in acceptance.test_negative_legs.pytestmark
        if mark.name == "parametrize"
    )

    assert list(positive.args[1]) == list(acceptance.POSITIVE_LEGS)
    assert list(negative.args[1]) == list(acceptance.NEGATIVE_CASES)

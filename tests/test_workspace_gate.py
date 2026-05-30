from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor.role import load_role
from agent_run_supervisor.runner import SupervisorRunner
from agent_run_supervisor.workspace import (
    WorkspaceValidationError,
    resolve_effective_cwd,
    validate_effective_cwd,
    workspace_hash,
)


def _role_with_roots(valid_role_dict: dict[str, Any], default_cwd: Path, roots: list[Path]):
    payload = dict(valid_role_dict)
    payload["workspace"] = dict(valid_role_dict["workspace"])
    payload["workspace"]["default_cwd"] = str(default_cwd)
    payload["workspace"]["allowed_roots"] = [str(p) for p in roots]
    payload["workspace"]["allowed_roots_security_boundary"] = False
    return load_role(payload)


def test_resolve_effective_cwd_uses_role_default_when_override_absent(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role = _role_with_roots(valid_role_dict, work, [work])

    resolved = resolve_effective_cwd(role, override=None)

    assert resolved == work.resolve()


def test_resolve_effective_cwd_uses_override_when_provided(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    sub = work / "sub"
    sub.mkdir(parents=True)
    role = _role_with_roots(valid_role_dict, work, [work])

    resolved = resolve_effective_cwd(role, override=str(sub))

    assert resolved == sub.resolve()


def test_validate_effective_cwd_accepts_default_inside_root(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role = _role_with_roots(valid_role_dict, work, [work])

    result = validate_effective_cwd(role, override=None)

    assert result.ok is True
    assert result.effective_cwd == work.resolve()
    assert result.matched_root == work.resolve()
    assert result.allowed_roots_security_boundary is False
    assert "not an OS" in result.disclaimer or "not a security boundary" in result.disclaimer


def test_validate_effective_cwd_accepts_subdir_inside_root(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    sub = work / "deep" / "nested"
    sub.mkdir(parents=True)
    role = _role_with_roots(valid_role_dict, work, [work])

    result = validate_effective_cwd(role, override=str(sub))

    assert result.ok is True
    assert result.effective_cwd == sub.resolve()


def test_validate_effective_cwd_rejects_override_outside_root(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    outside = tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    role = _role_with_roots(valid_role_dict, work, [work])

    with pytest.raises(WorkspaceValidationError) as exc_info:
        validate_effective_cwd(role, override=str(outside))

    message = str(exc_info.value)
    assert "allowed_roots" in message
    assert "not an OS" in message or "config validation" in message


def test_validate_effective_cwd_resolves_relative_segments(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    sub = work / "deep"
    sub.mkdir(parents=True)
    role = _role_with_roots(valid_role_dict, work, [work])

    result = validate_effective_cwd(role, override=str(sub / "..") )

    assert result.ok is True
    assert result.effective_cwd == work.resolve()


def test_validate_effective_cwd_rejects_default_outside_all_roots(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    elsewhere = tmp_path / "elsewhere"
    work.mkdir()
    elsewhere.mkdir()
    payload = dict(valid_role_dict)
    payload["workspace"] = dict(valid_role_dict["workspace"])
    payload["workspace"]["default_cwd"] = str(elsewhere)
    payload["workspace"]["allowed_roots"] = [str(work)]
    payload["workspace"]["allowed_roots_security_boundary"] = False
    role = load_role(payload)

    with pytest.raises(WorkspaceValidationError):
        validate_effective_cwd(role, override=None)


def test_dry_run_refuses_cwd_outside_allowed_roots_without_creating_artifacts(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    outside = tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    runs_dir = tmp_path / "runs"
    role = _role_with_roots(valid_role_dict, work, [work])
    runner = SupervisorRunner(runs_dir=runs_dir)

    with pytest.raises(WorkspaceValidationError):
        runner.dry_run(role=role, prompt="hi", cwd=str(outside))

    if runs_dir.exists():
        assert list(runs_dir.iterdir()) == []


def test_workspace_hash_is_deterministic_and_prefixed(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role = _role_with_roots(valid_role_dict, work, [work])
    result = validate_effective_cwd(role, override=None)

    first = workspace_hash(role, result)
    second = workspace_hash(role, result)

    assert first == second
    assert first.startswith("sha256:")


def test_workspace_hash_changes_with_effective_cwd(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    sub = work / "sub"
    sub.mkdir(parents=True)
    role = _role_with_roots(valid_role_dict, work, [work])

    at_root = workspace_hash(role, validate_effective_cwd(role, override=str(work)))
    at_sub = workspace_hash(role, validate_effective_cwd(role, override=str(sub)))

    assert at_root != at_sub


def test_workspace_hash_changes_with_allowed_roots(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    other = tmp_path / "other"
    work.mkdir()
    other.mkdir()
    role_narrow = _role_with_roots(valid_role_dict, work, [work])
    role_wide = _role_with_roots(valid_role_dict, work, [work, other])

    narrow = workspace_hash(role_narrow, validate_effective_cwd(role_narrow, override=str(work)))
    wide = workspace_hash(role_wide, validate_effective_cwd(role_wide, override=str(work)))

    assert narrow != wide


def test_workspace_hash_uses_validated_path_not_raw_override(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role = _role_with_roots(valid_role_dict, work, [work])

    canonical = workspace_hash(role, validate_effective_cwd(role, override=str(work)))
    # A non-canonical raw override that resolves to the same effective cwd must
    # produce the same hash — the hash binds the validated path, not raw input.
    noisy = workspace_hash(role, validate_effective_cwd(role, override=str(work) + "/."))

    assert canonical == noisy


def test_dry_run_accepts_cwd_inside_allowed_roots_and_records_metadata(
    tmp_path: Path, valid_role_dict: dict[str, Any]
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    role = _role_with_roots(valid_role_dict, work, [work])
    runner = SupervisorRunner(runs_dir=tmp_path / "runs")

    outcome = runner.dry_run(role=role, prompt="hi", cwd=str(work))

    import json

    metadata = json.loads((outcome.run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["allowed_roots_security_boundary"] is False
    assert metadata.get("effective_cwd") == str(work.resolve())
    assert "allowed_roots_disclaimer" in metadata

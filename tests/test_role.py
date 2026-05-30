from __future__ import annotations

import copy

import pytest

from agent_run_supervisor.role import (
    AgentRoleSpec,
    RoleValidationError,
    load_role,
    role_hash,
)


VALID_ROLE: dict = {
    "schema_version": 1,
    "role_id": "codex-reviewer",
    "display_name": "Codex Review",
    "description": "Read-only reviewer.",
    "runner": {
        "type": "acpx",
        "acpx_version": "0.10.0",
        "acpx_binary": "/usr/bin/acpx",
        "adapter_agent": "codex",
        "model": "gpt-5.5[low]",
    },
    "workspace": {
        "default_cwd": "/abs/path/to/repo",
        "allowed_roots": ["/abs/path/to/repo"],
        "allowed_roots_security_boundary": False,
    },
    "permissions": {
        "read": True,
        "search": True,
        "write": False,
        "execute": False,
        "terminal": False,
        "delete": False,
        "move": False,
        "fetch": False,
        "switch_mode": False,
        "other": False,
    },
    "session": {"strategy": "exec"},
    "limits": {
        "timeout_seconds": 900,
        "max_turns": 1,
        "max_output_bytes": 10_485_760,
    },
    "prompt": {
        "role_instruction": "You are a read-only reviewer.",
        "output_contract": "Return PASS/BLOCK/NOTES.",
    },
    "redaction": {
        "suppress_reads": True,
        "redact_prompt": True,
        "redact_stderr": True,
        "redact_metadata": True,
        "redact_env": True,
    },
}


def test_load_role_accepts_valid_mapping() -> None:
    role = load_role(VALID_ROLE)

    assert isinstance(role, AgentRoleSpec)
    assert role.role_id == "codex-reviewer"
    assert role.limits.timeout_seconds == 900
    assert role.permissions.read is True
    assert role.permissions.terminal is False
    assert role.workspace.allowed_roots_security_boundary is False


@pytest.mark.parametrize(
    "remove_key",
    ["schema_version", "role_id", "runner", "workspace", "permissions", "limits"],
)
def test_load_role_rejects_missing_required_top_level_keys(remove_key: str) -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec.pop(remove_key)

    with pytest.raises(RoleValidationError) as excinfo:
        load_role(spec)
    assert remove_key in str(excinfo.value)


def test_load_role_rejects_unknown_permission_kind() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["permissions"]["delete_world"] = True

    with pytest.raises(RoleValidationError) as excinfo:
        load_role(spec)
    assert "delete_world" in str(excinfo.value)


def test_load_role_rejects_security_boundary_claim_for_allowed_roots() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["workspace"]["allowed_roots_security_boundary"] = True

    with pytest.raises(RoleValidationError) as excinfo:
        load_role(spec)
    assert "allowed_roots_security_boundary" in str(excinfo.value)
    assert "not a security boundary" in str(excinfo.value).lower()


def test_load_role_rejects_bad_schema_version() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["schema_version"] = 999

    with pytest.raises(RoleValidationError):
        load_role(spec)


def test_load_role_defaults_session_to_exec_without_lease() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec.pop("session", None)

    role = load_role(spec)

    assert role.session.strategy == "exec"
    assert role.session.lease_seconds is None


def test_load_role_accepts_persistent_session_strategy() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["session"] = {"strategy": "persistent"}

    role = load_role(spec)

    assert role.session.strategy == "persistent"
    # Persistent sessions get a bounded default lease so concurrency control
    # always has a finite expiry to work with.
    assert isinstance(role.session.lease_seconds, int)
    assert role.session.lease_seconds > 0


def test_load_role_accepts_explicit_persistent_lease_seconds() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["session"] = {"strategy": "persistent", "lease_seconds": 120}

    role = load_role(spec)

    assert role.session.strategy == "persistent"
    assert role.session.lease_seconds == 120


def test_load_role_rejects_unknown_session_strategy() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["session"] = {"strategy": "daemon"}

    with pytest.raises(RoleValidationError) as excinfo:
        load_role(spec)
    assert "strategy" in str(excinfo.value)
    assert "daemon" in str(excinfo.value)


def test_load_role_rejects_nonpositive_persistent_lease_seconds() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["session"] = {"strategy": "persistent", "lease_seconds": 0}

    with pytest.raises(RoleValidationError) as excinfo:
        load_role(spec)
    assert "lease_seconds" in str(excinfo.value)


def test_load_role_rejects_oversized_persistent_lease_seconds() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["session"] = {"strategy": "persistent", "lease_seconds": 10_000_000}

    with pytest.raises(RoleValidationError) as excinfo:
        load_role(spec)
    assert "lease_seconds" in str(excinfo.value)


def test_load_role_rejects_lease_seconds_for_exec_strategy() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["session"] = {"strategy": "exec", "lease_seconds": 120}

    with pytest.raises(RoleValidationError) as excinfo:
        load_role(spec)
    assert "lease_seconds" in str(excinfo.value)


def test_load_role_rejects_unknown_session_keys() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["session"] = {"strategy": "persistent", "shell_access": True}

    with pytest.raises(RoleValidationError) as excinfo:
        load_role(spec)
    assert "unknown" in str(excinfo.value).lower()
    assert "shell_access" in str(excinfo.value)


def test_load_role_rejects_nonpositive_limits() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec["limits"]["timeout_seconds"] = 0

    with pytest.raises(RoleValidationError):
        load_role(spec)


def test_role_hash_is_deterministic_for_same_input() -> None:
    role = load_role(VALID_ROLE)

    assert role_hash(role) == role_hash(role)


def test_role_hash_changes_when_permissions_change() -> None:
    role_a = load_role(VALID_ROLE)
    spec_b = copy.deepcopy(VALID_ROLE)
    spec_b["permissions"]["write"] = True
    role_b = load_role(spec_b)

    assert role_hash(role_a) != role_hash(role_b)


def test_load_role_defaults_redaction_to_safe_values() -> None:
    spec = copy.deepcopy(VALID_ROLE)
    spec.pop("redaction")

    role = load_role(spec)

    assert role.redaction.suppress_reads is True
    assert role.redaction.redact_prompt is True
    assert role.redaction.redact_stderr is True
    assert role.redaction.redact_env is True
    assert role.redaction.redact_metadata is True

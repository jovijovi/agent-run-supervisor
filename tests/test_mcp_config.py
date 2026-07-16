"""Native role-bound acpx ``--mcp-config`` binding resolution.

These tests pin the optional role ``runner.mcp_config`` binding helper: an
absolute JSON config with a top-level ``mcpServers`` array resolves to a
canonical path + content-SHA binding; anything else fails closed with a
diagnostic that never embeds config file content (configs may carry secrets).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from agent_run_supervisor.mcp_config import (
    McpConfigBinding,
    McpConfigError,
    resolve_mcp_config,
)
from agent_run_supervisor.role import load_role


def _role(valid_role_dict: dict[str, Any], mcp_config: str | None):
    payload = copy.deepcopy(valid_role_dict)
    payload["runner"]["mcp_config"] = mcp_config
    return load_role(payload)


def _write_config(path: Path, payload: Any) -> bytes:
    raw = json.dumps(payload).encode("utf-8")
    path.write_bytes(raw)
    return raw


def test_resolve_returns_none_when_role_has_no_mcp_config(valid_role_dict) -> None:
    role = load_role(copy.deepcopy(valid_role_dict))

    assert resolve_mcp_config(role) is None


def test_resolve_binds_canonical_path_and_content_sha(tmp_path, valid_role_dict) -> None:
    config = tmp_path / "mcp.json"
    raw = _write_config(config, {"mcpServers": []})
    role = _role(valid_role_dict, str(config))

    binding = resolve_mcp_config(role)

    assert binding == McpConfigBinding(
        path=os.path.realpath(config),
        sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
    )


def test_resolve_canonicalizes_symlinked_path(tmp_path, valid_role_dict) -> None:
    real = tmp_path / "real-mcp.json"
    raw = _write_config(real, {"mcpServers": [{"name": "probe"}]})
    link = tmp_path / "link-mcp.json"
    link.symlink_to(real)
    role = _role(valid_role_dict, str(link))

    binding = resolve_mcp_config(role)

    assert binding.path == os.path.realpath(real)
    assert binding.sha256 == "sha256:" + hashlib.sha256(raw).hexdigest()


def test_resolve_sha_tracks_same_path_content_drift(tmp_path, valid_role_dict) -> None:
    config = tmp_path / "mcp.json"
    _write_config(config, {"mcpServers": []})
    role = _role(valid_role_dict, str(config))
    before = resolve_mcp_config(role)

    _write_config(config, {"mcpServers": [{"name": "added"}]})
    after = resolve_mcp_config(role)

    assert before.path == after.path
    assert before.sha256 != after.sha256


# --- non-regular file rejection (fail closed, never open/block) --------------
#
# A declared FIFO must never block validation: ``open()`` on a reader-less FIFO
# hangs forever, so resolution must reject non-regular file types before any
# blocking open. Each probe runs on a watchdog thread so a regression that
# blocks fails the test in bounded time instead of hanging the suite.

_RESOLVE_BOUND_SECONDS = 5.0


def _resolve_bounded(role) -> dict[str, Any]:
    outcome: dict[str, Any] = {}

    def _target() -> None:
        try:
            outcome["binding"] = resolve_mcp_config(role)
        except BaseException as exc:  # noqa: BLE001 - capture for assertion
            outcome["error"] = exc

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(_RESOLVE_BOUND_SECONDS)
    if worker.is_alive():
        pytest.fail(
            "resolve_mcp_config blocked on a non-regular file instead of "
            "failing closed"
        )
    return outcome


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFOs")
def test_resolve_rejects_fifo_without_blocking(tmp_path, valid_role_dict) -> None:
    fifo = tmp_path / "mcp.fifo"
    os.mkfifo(fifo)
    role = _role(valid_role_dict, str(fifo))

    outcome = _resolve_bounded(role)

    error = outcome.get("error")
    assert isinstance(error, McpConfigError)
    assert "regular file" in str(error)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFOs")
def test_resolve_rejects_symlink_to_fifo_without_blocking(
    tmp_path, valid_role_dict
) -> None:
    fifo = tmp_path / "real.fifo"
    os.mkfifo(fifo)
    link = tmp_path / "mcp.json"
    link.symlink_to(fifo)
    role = _role(valid_role_dict, str(link))

    outcome = _resolve_bounded(role)

    error = outcome.get("error")
    assert isinstance(error, McpConfigError)
    assert "regular file" in str(error)


def test_resolve_rejects_directory_as_non_regular(tmp_path, valid_role_dict) -> None:
    config_dir = tmp_path / "mcp-config-dir"
    config_dir.mkdir()
    role = _role(valid_role_dict, str(config_dir))

    outcome = _resolve_bounded(role)

    error = outcome.get("error")
    assert isinstance(error, McpConfigError)
    assert "regular file" in str(error)


def test_resolve_fails_closed_on_missing_file(tmp_path, valid_role_dict) -> None:
    role = _role(valid_role_dict, str(tmp_path / "absent.json"))

    with pytest.raises(McpConfigError):
        resolve_mcp_config(role)


def test_resolve_rejects_invalid_json_without_leaking_content(
    tmp_path, valid_role_dict
) -> None:
    config = tmp_path / "mcp.json"
    config.write_text('{"mcpServers": [SECRET-TOKEN-VALUE', encoding="utf-8")
    role = _role(valid_role_dict, str(config))

    with pytest.raises(McpConfigError) as excinfo:
        resolve_mcp_config(role)

    assert "SECRET-TOKEN-VALUE" not in str(excinfo.value)


@pytest.mark.parametrize(
    "payload",
    [
        ["SECRET-TOKEN-VALUE"],  # top-level array, not an object
        {"servers": ["SECRET-TOKEN-VALUE"]},  # missing mcpServers key
        {"mcpServers": {"name": "SECRET-TOKEN-VALUE"}},  # object, not array
        {"mcpServers": "SECRET-TOKEN-VALUE"},  # string, not array
    ],
)
def test_resolve_requires_top_level_mcp_servers_array(
    tmp_path, valid_role_dict, payload
) -> None:
    config = tmp_path / "mcp.json"
    _write_config(config, payload)
    role = _role(valid_role_dict, str(config))

    with pytest.raises(McpConfigError) as excinfo:
        resolve_mcp_config(role)

    assert "mcpServers" in str(excinfo.value)
    assert "SECRET-TOKEN-VALUE" not in str(excinfo.value)

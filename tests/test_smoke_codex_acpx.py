from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import smoke_codex_acpx as smoke


def _result(payload: dict) -> dict:
    return {"returncode": 0, "stdout": json.dumps(payload), "stderr": "", "json": payload}


def test_validate_model_id_rejects_bare_codex_model() -> None:
    with pytest.raises(smoke.EnvironmentNotReady, match="advertised ACP model ID"):
        smoke.validate_model_id("gpt-5.5")


def test_build_roles_use_advertised_model_and_no_tool_permissions(tmp_path: Path) -> None:
    exec_role = smoke.build_role(
        tmp_path / "work",
        role_id="codex-smoke-exec",
        strategy="exec",
        model=smoke.DEFAULT_MODEL,
        acpx_timeout_seconds=7,
    )
    persistent_role = smoke.build_role(
        tmp_path / "work",
        role_id="codex-smoke-session",
        strategy="persistent",
        model=smoke.DEFAULT_MODEL,
        acpx_timeout_seconds=7,
    )

    assert exec_role["runner"]["adapter_agent"] == "codex"
    assert exec_role["runner"]["model"] == "gpt-5.5[xhigh]"
    assert exec_role["runner"]["acpx_binary"] is None
    assert exec_role["session"]["strategy"] == "exec"
    assert persistent_role["session"]["strategy"] == "persistent"
    assert set(exec_role["permissions"].values()) == {False}
    assert set(persistent_role["permissions"].values()) == {False}


def test_run_smoke_drives_one_shot_before_persistent_session(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run_cli(args: list[str], *, timeout: int) -> dict:
        calls.append(args)
        if args[0] == "validate-role":
            return _result({"valid": True})
        if args[0] == "run":
            return _result(
                {
                    "status": "completed",
                    "final_message": smoke.ONE_SHOT_MARKER,
                    "business_verdict": None,
                    "run_dir": str(tmp_path / "runs" / "run-1"),
                }
            )
        if args[:2] == ["session", "create"]:
            return _result(
                {"session_id": smoke.SESSION_ID, "state": "open", "business_verdict": None}
            )
        if args[:2] == ["session", "send"]:
            prompt_file = Path(args[args.index("--prompt-file") + 1])
            marker = smoke.SESSION_TURN1_MARKER if "turn1" in prompt_file.name else smoke.SESSION_TURN2_MARKER
            return _result(
                {
                    "session_id": smoke.SESSION_ID,
                    "status": "completed",
                    "turn_id": prompt_file.stem,
                    "final_message": marker,
                    "business_verdict": None,
                }
            )
        if args[:2] == ["session", "status"]:
            return _result({"session_id": smoke.SESSION_ID, "ok": True, "business_verdict": None})
        if args[:2] == ["session", "close"]:
            return _result(
                {"session_id": smoke.SESSION_ID, "state": "closed", "closed": True, "business_verdict": None}
            )
        raise AssertionError(f"unexpected CLI args: {args!r}")

    monkeypatch.setattr(smoke, "run_cli", fake_run_cli)
    monkeypatch.setattr(smoke, "make_session_name", lambda: "codex-smoke-fixed")

    summary = smoke.run_smoke(
        model=smoke.DEFAULT_MODEL,
        acpx_timeout_seconds=1,
        scratch=tmp_path / "scratch",
        runs_dir=tmp_path / "runs",
        sessions_dir=tmp_path / "sessions",
    )

    assert summary["ok"] is True
    assert summary["model"] == "gpt-5.5[xhigh]"
    assert summary["one_shot"]["marker"] == smoke.ONE_SHOT_MARKER
    assert summary["persistent_session"]["turn1"]["marker"] == smoke.SESSION_TURN1_MARKER
    assert summary["persistent_session"]["turn2"]["marker"] == smoke.SESSION_TURN2_MARKER
    assert calls.index(next(args for args in calls if args[0] == "run")) < calls.index(
        next(args for args in calls if args[:2] == ["session", "create"])
    )

    exec_role = json.loads((tmp_path / "scratch" / "codex-smoke-exec-role.json").read_text(encoding="utf-8"))
    session_role = json.loads((tmp_path / "scratch" / "codex-smoke-session-role.json").read_text(encoding="utf-8"))
    assert exec_role["runner"]["model"] == "gpt-5.5[xhigh]"
    assert session_role["runner"]["model"] == "gpt-5.5[xhigh]"


def test_run_smoke_writes_helper_scratch_artifacts_private(monkeypatch, tmp_path: Path) -> None:
    def fake_run_cli(args: list[str], *, timeout: int) -> dict:
        if args[0] == "validate-role":
            return _result({"valid": True})
        if args[0] == "run":
            return _result(
                {"status": "completed", "final_message": smoke.ONE_SHOT_MARKER, "business_verdict": None}
            )
        if args[:2] == ["session", "create"]:
            return _result(
                {"session_id": smoke.SESSION_ID, "state": "open", "business_verdict": None}
            )
        if args[:2] == ["session", "send"]:
            prompt_file = Path(args[args.index("--prompt-file") + 1])
            marker = smoke.SESSION_TURN1_MARKER if "turn1" in prompt_file.name else smoke.SESSION_TURN2_MARKER
            return _result(
                {
                    "session_id": smoke.SESSION_ID,
                    "status": "completed",
                    "turn_id": prompt_file.stem,
                    "final_message": marker,
                    "business_verdict": None,
                }
            )
        if args[:2] == ["session", "status"]:
            return _result({"session_id": smoke.SESSION_ID, "ok": True, "business_verdict": None})
        if args[:2] == ["session", "close"]:
            return _result(
                {"session_id": smoke.SESSION_ID, "state": "closed", "closed": True, "business_verdict": None}
            )
        raise AssertionError(f"unexpected CLI args: {args!r}")

    monkeypatch.setattr(smoke, "run_cli", fake_run_cli)

    scratch = tmp_path / "scratch"
    smoke.run_smoke(
        model=smoke.DEFAULT_MODEL,
        acpx_timeout_seconds=1,
        scratch=scratch,
        runs_dir=tmp_path / "runs",
        sessions_dir=tmp_path / "sessions",
    )

    private_dirs = [scratch, scratch / "work", tmp_path / "runs", tmp_path / "sessions"]
    private_files = [
        scratch / "codex-smoke-exec-role.json",
        scratch / "codex-smoke-session-role.json",
        scratch / "one-shot.prompt.txt",
        scratch / "turn1.prompt.txt",
        scratch / "turn2.prompt.txt",
    ]
    assert {oct(path.stat().st_mode & 0o777) for path in private_dirs} == {"0o700"}
    assert {oct(path.stat().st_mode & 0o777) for path in private_files} == {"0o600"}


def test_run_smoke_closes_persistent_session_after_marker_failure(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run_cli(args: list[str], *, timeout: int) -> dict:
        calls.append(args)
        if args[0] == "validate-role":
            return _result({"valid": True})
        if args[0] == "run":
            return _result(
                {"status": "completed", "final_message": smoke.ONE_SHOT_MARKER, "business_verdict": None}
            )
        if args[:2] == ["session", "create"]:
            return _result(
                {"session_id": smoke.SESSION_ID, "state": "open", "business_verdict": None}
            )
        if args[:2] == ["session", "send"]:
            return _result(
                {
                    "session_id": smoke.SESSION_ID,
                    "status": "completed",
                    "turn_id": "turn-1",
                    "final_message": "WRONG_MARKER",
                    "business_verdict": None,
                }
            )
        if args[:2] == ["session", "close"]:
            return _result(
                {"session_id": smoke.SESSION_ID, "state": "closed", "closed": True, "business_verdict": None}
            )
        raise AssertionError(f"unexpected CLI args: {args!r}")

    monkeypatch.setattr(smoke, "run_cli", fake_run_cli)

    with pytest.raises(smoke.SmokeError, match="WRONG_MARKER"):
        smoke.run_smoke(
            model=smoke.DEFAULT_MODEL,
            acpx_timeout_seconds=1,
            scratch=tmp_path / "scratch",
            runs_dir=tmp_path / "runs",
            sessions_dir=tmp_path / "sessions",
        )

    assert calls[-1][:2] == ["session", "close"]
    assert sum(1 for args in calls if args[:2] == ["session", "close"]) == 1

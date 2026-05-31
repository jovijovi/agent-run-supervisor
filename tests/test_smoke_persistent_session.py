from __future__ import annotations

import pytest

from scripts import smoke_persistent_session as smoke


def _result(payload: dict) -> dict:
    return {"returncode": 0, "stdout": "", "stderr": "", "json": payload}


def test_smoke_preflight_requires_npx_for_null_acpx_binary(monkeypatch) -> None:
    def fake_which(name: str) -> str | None:
        if name == "acpx":
            return "/usr/local/bin/acpx"
        return None

    monkeypatch.setattr(smoke.shutil, "which", fake_which)

    with pytest.raises(smoke.EnvironmentNotReady, match="npx is required"):
        smoke.preflight()


def test_smoke_session_names_are_unique_per_invocation(monkeypatch) -> None:
    values = iter(["a" * 32, "b" * 32])

    class FakeUUID:
        def __init__(self, hex_value: str) -> None:
            self.hex = hex_value

    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: FakeUUID(next(values)))

    first = smoke.make_session_name()
    second = smoke.make_session_name()

    assert first == f"{smoke.SESSION_NAME_PREFIX}-aaaaaaaaaaaa"
    assert second == f"{smoke.SESSION_NAME_PREFIX}-bbbbbbbbbbbb"
    assert first != second


def test_smoke_closes_created_session_after_create_validation_failure(
    monkeypatch, tmp_path
) -> None:
    calls: list[list[str]] = []
    raw_close_calls: list[dict] = []

    def fake_run_cli(args: list[str], *, timeout: int) -> dict:
        calls.append(args)
        if args[0] == "validate-role":
            return _result({"valid": True})
        if args[:2] == ["session", "create"]:
            return _result(
                {
                    "session_id": smoke.SESSION_ID,
                    "state": "unexpected",
                    "business_verdict": None,
                }
            )
        if args[:2] == ["session", "close"]:
            return _result(
                {
                    "session_id": smoke.SESSION_ID,
                    "state": "closed",
                    "closed": True,
                    "business_verdict": None,
                }
            )
        raise AssertionError(f"unexpected CLI args: {args!r}")

    monkeypatch.setattr(smoke, "run_cli", fake_run_cli)
    monkeypatch.setattr(
        smoke,
        "best_effort_close_acpx_session",
        lambda **kwargs: raw_close_calls.append(kwargs),
    )

    with pytest.raises(smoke.SmokeError, match="unexpected"):
        smoke.run_smoke(
            acpx_timeout_seconds=1,
            scratch=tmp_path / "scratch",
            sessions_dir=tmp_path / "sessions",
        )

    assert calls[-1][:2] == ["session", "close"]
    assert sum(1 for args in calls if args[:2] == ["session", "close"]) == 1
    assert raw_close_calls == []


def test_smoke_raw_closes_after_create_command_failure(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []
    raw_close_calls: list[dict] = []

    def fake_run_cli(args: list[str], *, timeout: int) -> dict:
        calls.append(args)
        if args[0] == "validate-role":
            return _result({"valid": True})
        if args[:2] == ["session", "create"]:
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": "create failed after launching acpx",
                "json": None,
            }
        raise AssertionError(f"unexpected CLI args: {args!r}")

    monkeypatch.setattr(smoke, "run_cli", fake_run_cli)
    monkeypatch.setattr(
        smoke,
        "best_effort_close_acpx_session",
        lambda **kwargs: raw_close_calls.append(kwargs),
    )

    with pytest.raises(smoke.SmokeError, match="session create: exit 1"):
        smoke.run_smoke(
            acpx_timeout_seconds=1,
            scratch=tmp_path / "scratch",
            sessions_dir=tmp_path / "sessions",
        )

    assert raw_close_calls
    assert raw_close_calls[0]["session_name"].startswith(smoke.SESSION_NAME_PREFIX + "-")
    assert raw_close_calls[0]["cwd"] == tmp_path / "scratch" / "work"


def test_smoke_closes_created_session_best_effort_after_marker_failure(
    monkeypatch, tmp_path
) -> None:
    calls: list[list[str]] = []

    def fake_run_cli(args: list[str], *, timeout: int) -> dict:
        calls.append(args)
        if args[0] == "validate-role":
            return _result({"valid": True})
        if args[:2] == ["session", "create"]:
            return _result(
                {
                    "session_id": smoke.SESSION_ID,
                    "state": "open",
                    "business_verdict": None,
                }
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
                {
                    "session_id": smoke.SESSION_ID,
                    "state": "closed",
                    "closed": True,
                    "business_verdict": None,
                }
            )
        raise AssertionError(f"unexpected CLI args: {args!r}")

    monkeypatch.setattr(smoke, "run_cli", fake_run_cli)

    with pytest.raises(smoke.SmokeError, match="WRONG_MARKER"):
        smoke.run_smoke(
            acpx_timeout_seconds=1,
            scratch=tmp_path / "scratch",
            sessions_dir=tmp_path / "sessions",
        )

    assert calls[-1][:2] == ["session", "close"]
    assert sum(1 for args in calls if args[:2] == ["session", "close"]) == 1

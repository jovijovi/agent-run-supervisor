#!/usr/bin/env python3
"""Real local Codex/acpx smoke helper for agent-run-supervisor.

This stdlib-only helper drives the existing supervisor CLI through the two Codex
surfaces that matter for local orchestration confidence:

1. one-shot exec: ``run --role <exec-role> --prompt-file <prompt>``;
2. persistent session: ``session create -> send turn1 -> send turn2 -> status -> close``.

It intentionally uses ``runner.acpx_binary = null`` so the supervisor compiler takes
the pinned ``npx -y acpx@0.10.0`` path. The default model is the concrete Codex ACP
advertised model id ``gpt-5.5[xhigh]``; a bare model id such as ``gpt-5.5`` is
rejected before launching anything because Codex ACP can reject non-advertised ids.

Scope boundary:

- launches only explicit local acpx/Codex smoke commands;
- uses no file-reading/tool/editing prompt intent;
- never touches Sachima, Hermes Gateway, IM delivery, public ingress, production
  config, automatic replies, or agent-to-agent routing;
- writes temp artifacts under a private temp directory and cleans them by default.

Exit codes: ``0`` smoke passed; ``1`` lifecycle/verification failed; ``2`` local
environment precondition failed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SRC_DIR = REPO / "src"

ACPX_VERSION = "0.10.0"
ADAPTER_AGENT = "codex"
DEFAULT_MODEL = "gpt-5.5[xhigh]"

ONE_SHOT_MARKER = "AGENT_RUN_SUPERVISOR_CODEX_OK"
SESSION_TURN1_MARKER = "CODEX_SESSION_TURN1_OK"
SESSION_TURN2_MARKER = "CODEX_SESSION_TURN2_OK"

SESSION_ID = "codex-smoke"
SESSION_NAME_PREFIX = "codex-smoke"

ONE_SHOT_PROMPT = (
    "Codex/acpx one-shot smoke. Do not inspect files, run tools, or edit anything. "
    f"Reply with exactly this token and nothing else: {ONE_SHOT_MARKER}"
)
SESSION_TURN1_PROMPT = (
    "Codex/acpx persistent session smoke turn 1. Do not inspect files, run tools, "
    f"or edit anything. Reply exactly: {SESSION_TURN1_MARKER}"
)
SESSION_TURN2_PROMPT = (
    "Codex/acpx persistent session smoke turn 2. Do not inspect files, run tools, "
    f"or edit anything. Reply exactly: {SESSION_TURN2_MARKER}"
)

DEFAULT_ACPX_TIMEOUT_SECONDS = 240
LAUNCH_BUFFER_SECONDS = 180
LOCAL_CALL_TIMEOUT_SECONDS = 60


class SmokeError(RuntimeError):
    """A smoke lifecycle or verification step failed."""


class EnvironmentNotReady(RuntimeError):
    """A local precondition for real Codex/acpx smoke is missing."""


def validate_model_id(model: str) -> str:
    """Require a concrete Codex ACP advertised model id.

    Codex ACP advertises effort-qualified model ids such as ``gpt-5.5[xhigh]``.
    Passing a bare id like ``gpt-5.5`` can fail at runtime with "the ACP agent did
    not advertise that model", so the smoke helper fails before launching acpx.
    """
    if not isinstance(model, str) or not model:
        raise EnvironmentNotReady("model must be a non-empty Codex ACP model id")
    if "[" not in model or not model.endswith("]"):
        raise EnvironmentNotReady(
            "Codex smoke requires an advertised ACP model ID such as "
            "'gpt-5.5[xhigh]'; bare ids such as 'gpt-5.5' are rejected."
        )
    return model


def build_role(
    work_dir: Path,
    *,
    role_id: str,
    strategy: str,
    model: str,
    acpx_timeout_seconds: int,
) -> dict[str, Any]:
    """Build a minimal no-tool Codex role for exec or persistent-session smoke."""
    if strategy not in {"exec", "persistent"}:
        raise ValueError("strategy must be 'exec' or 'persistent'")
    model = validate_model_id(model)
    permissions = {
        "read": False,
        "search": False,
        "write": False,
        "execute": False,
        "terminal": False,
        "delete": False,
        "move": False,
        "fetch": False,
        "switch_mode": False,
        "other": False,
    }
    return {
        "schema_version": 1,
        "role_id": role_id,
        "display_name": "Codex acpx smoke",
        "description": "Local no-tool Codex/acpx connectivity smoke.",
        "runner": {
            "type": "acpx",
            "acpx_version": ACPX_VERSION,
            "acpx_binary": None,
            "adapter_agent": ADAPTER_AGENT,
            "model": model,
        },
        "workspace": {
            "default_cwd": str(work_dir),
            "allowed_roots": [str(work_dir)],
            "allowed_roots_security_boundary": False,
        },
        "permissions": permissions,
        "session": {"strategy": strategy},
        "limits": {
            "timeout_seconds": acpx_timeout_seconds,
            "max_turns": 1,
            "max_output_bytes": 10_485_760,
        },
        "prompt": {
            "role_instruction": "Reply with the requested token only.",
            "output_contract": "Return the exact requested token.",
        },
        "redaction": {
            "suppress_reads": True,
            "redact_prompt": True,
            "redact_stderr": True,
            "redact_metadata": True,
            "redact_env": True,
        },
    }


def make_session_name() -> str:
    """Return a unique acpx named session for this smoke invocation."""
    return f"{SESSION_NAME_PREFIX}-{uuid.uuid4().hex[:12]}"


def _cli_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(SRC_DIR), existing) if p)
    return env


def run_cli(args: list[str], *, timeout: int) -> dict[str, Any]:
    """Invoke the local supervisor CLI and parse its last JSON stdout line."""
    completed = subprocess.run(
        [sys.executable, "-m", "agent_run_supervisor", *args],
        capture_output=True,
        text=True,
        check=False,
        env=_cli_env(),
        cwd=str(REPO),
        timeout=timeout,
    )
    payload: Any = None
    for line in reversed(completed.stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = None
        break
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "json": payload,
    }


def best_effort_close_acpx_session(
    *, session_name: str, cwd: Path, timeout: int
) -> None:
    """Close a named acpx session after a create failure, without masking errors."""
    argv = [
        "npx",
        "-y",
        f"acpx@{ACPX_VERSION}",
        "--format",
        "json",
        "--json-strict",
        "--cwd",
        str(cwd),
        ADAPTER_AGENT,
        "sessions",
        "close",
        session_name,
    ]
    try:
        subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            env=_cli_env(),
            cwd=str(REPO),
            timeout=timeout,
        )
    except Exception:
        pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def _require_json(result: dict[str, Any], step: str) -> dict[str, Any]:
    payload = result["json"]
    if not isinstance(payload, dict):
        raise SmokeError(
            f"{step}: expected JSON object on stdout, got "
            f"rc={result['returncode']} stderr={result['stderr'].strip()!r}"
        )
    return payload


def _check_business_verdict_null(payload: dict[str, Any], step: str) -> None:
    _require(
        "business_verdict" in payload and payload["business_verdict"] is None,
        f"{step}: business_verdict must be null, got {payload.get('business_verdict')!r}",
    )


def preflight(*, model: str) -> dict[str, Any]:
    """Confirm local binaries for the pinned npx-based Codex/acpx path."""
    validate_model_id(model)
    npx = shutil.which("npx")
    if npx is None:
        raise EnvironmentNotReady(
            "npx is required: this smoke uses runner.acpx_binary=null so the "
            f"supervisor invokes 'npx -y acpx@{ACPX_VERSION}'"
        )
    codex = os.environ.get("CODEX_PATH") or shutil.which("codex")
    if not codex:
        raise EnvironmentNotReady(
            "Codex CLI is required via CODEX_PATH or PATH for the acpx codex adapter"
        )
    return {"npx": npx, "codex": codex, "model": model}


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def _write_text_private(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text_private(path, json.dumps(payload, indent=2, sort_keys=True))


def run_smoke(
    *,
    model: str,
    acpx_timeout_seconds: int,
    scratch: Path,
    runs_dir: Path,
    sessions_dir: Path,
) -> dict[str, Any]:
    """Run one-shot Codex smoke first, then persistent session smoke."""
    model = validate_model_id(model)
    session_name = make_session_name()
    work_dir = scratch / "work"
    _mkdir_private(scratch)
    _mkdir_private(work_dir)
    _mkdir_private(runs_dir)
    _mkdir_private(sessions_dir)

    exec_role_path = scratch / "codex-smoke-exec-role.json"
    session_role_path = scratch / "codex-smoke-session-role.json"
    one_prompt = scratch / "one-shot.prompt.txt"
    turn1_prompt = scratch / "turn1.prompt.txt"
    turn2_prompt = scratch / "turn2.prompt.txt"

    _write_json(
        exec_role_path,
        build_role(
            work_dir,
            role_id="codex-smoke-exec",
            strategy="exec",
            model=model,
            acpx_timeout_seconds=acpx_timeout_seconds,
        ),
    )
    _write_json(
        session_role_path,
        build_role(
            work_dir,
            role_id="codex-smoke-session",
            strategy="persistent",
            model=model,
            acpx_timeout_seconds=acpx_timeout_seconds,
        ),
    )
    _write_text_private(one_prompt, ONE_SHOT_PROMPT)
    _write_text_private(turn1_prompt, SESSION_TURN1_PROMPT)
    _write_text_private(turn2_prompt, SESSION_TURN2_PROMPT)

    launch_timeout = acpx_timeout_seconds + LAUNCH_BUFFER_SECONDS
    sessions_arg = ["--sessions-dir", str(sessions_dir)]
    session_common = ["--role", str(session_role_path), "--session-id", SESSION_ID]
    created = False
    closed = False
    create_attempted = False

    def best_effort_close() -> None:
        if closed:
            return
        if created:
            try:
                cleanup = run_cli(
                    ["session", "close", *session_common, *sessions_arg],
                    timeout=launch_timeout,
                )
                if cleanup.get("returncode") == 0:
                    return
            except Exception:
                pass
        if create_attempted:
            best_effort_close_acpx_session(
                session_name=session_name,
                cwd=work_dir,
                timeout=launch_timeout,
            )

    try:
        for role_path, step in (
            (exec_role_path, "validate exec role"),
            (session_role_path, "validate session role"),
        ):
            res = run_cli(["validate-role", str(role_path)], timeout=LOCAL_CALL_TIMEOUT_SECONDS)
            _require(res["returncode"] == 0, f"{step}: exit {res['returncode']}: {res['stderr'].strip()!r}")
            payload = _require_json(res, step)
            _require(payload.get("valid") is True, f"{step}: role not reported valid")

        # 1. one-shot exec -------------------------------------------------
        res = run_cli(
            [
                "run",
                "--role",
                str(exec_role_path),
                "--prompt-file",
                str(one_prompt),
                "--runs-dir",
                str(runs_dir),
            ],
            timeout=launch_timeout,
        )
        _require(res["returncode"] == 0, f"one-shot run: exit {res['returncode']}: {res['stderr'].strip()!r}")
        one = _require_json(res, "one-shot run")
        _require(one.get("status") == "completed", f"one-shot run: status={one.get('status')!r}")
        observed_one = (one.get("final_message") or "").strip()
        _require(
            observed_one == ONE_SHOT_MARKER,
            f"one-shot run: final_message={observed_one!r}, want {ONE_SHOT_MARKER!r}",
        )
        _check_business_verdict_null(one, "one-shot run")

        # 2. persistent session create ------------------------------------
        create_attempted = True
        res = run_cli(
            [
                "session",
                "create",
                *session_common,
                "--session-name",
                session_name,
                *sessions_arg,
            ],
            timeout=launch_timeout,
        )
        _require(res["returncode"] == 0, f"session create: exit {res['returncode']}: {res['stderr'].strip()!r}")
        created = True
        create = _require_json(res, "session create")
        _require(create.get("state") == "open", f"session create: state={create.get('state')!r}, want 'open'")
        _check_business_verdict_null(create, "session create")

        # 3. send two turns ------------------------------------------------
        res = run_cli(
            ["session", "send", *session_common, "--prompt-file", str(turn1_prompt), *sessions_arg],
            timeout=launch_timeout,
        )
        _require(res["returncode"] == 0, f"session send #1: exit {res['returncode']}: {res['stderr'].strip()!r}")
        turn1 = _require_json(res, "session send #1")
        _require(turn1.get("status") == "completed", f"session send #1: status={turn1.get('status')!r}")
        observed1 = (turn1.get("final_message") or "").strip()
        _require(
            observed1 == SESSION_TURN1_MARKER,
            f"session send #1: final_message={observed1!r}, want {SESSION_TURN1_MARKER!r}",
        )
        _check_business_verdict_null(turn1, "session send #1")

        res = run_cli(
            ["session", "send", *session_common, "--prompt-file", str(turn2_prompt), *sessions_arg],
            timeout=launch_timeout,
        )
        _require(res["returncode"] == 0, f"session send #2: exit {res['returncode']}: {res['stderr'].strip()!r}")
        turn2 = _require_json(res, "session send #2")
        _require(turn2.get("status") == "completed", f"session send #2: status={turn2.get('status')!r}")
        observed2 = (turn2.get("final_message") or "").strip()
        _require(
            observed2 == SESSION_TURN2_MARKER,
            f"session send #2: final_message={observed2!r}, want {SESSION_TURN2_MARKER!r}",
        )
        _check_business_verdict_null(turn2, "session send #2")

        # 4. status + close -----------------------------------------------
        res = run_cli(["session", "status", *session_common, *sessions_arg], timeout=launch_timeout)
        _require(res["returncode"] == 0, f"session status: exit {res['returncode']}: {res['stderr'].strip()!r}")
        status = _require_json(res, "session status")
        _require(status.get("ok") is True, f"session status: ok={status.get('ok')!r}")
        _check_business_verdict_null(status, "session status")

        res = run_cli(["session", "close", *session_common, *sessions_arg], timeout=launch_timeout)
        _require(res["returncode"] == 0, f"session close: exit {res['returncode']}: {res['stderr'].strip()!r}")
        close = _require_json(res, "session close")
        _require(close.get("state") == "closed", f"session close: state={close.get('state')!r}, want 'closed'")
        _require(close.get("closed") is True, "session close: closed flag not true")
        _check_business_verdict_null(close, "session close")
        closed = True

        return {
            "smoke": "codex-acpx-one-shot-and-session",
            "ok": True,
            "acpx_version": ACPX_VERSION,
            "adapter_agent": ADAPTER_AGENT,
            "model": model,
            "one_shot": {"status": one.get("status"), "marker": observed_one},
            "persistent_session": {
                "session_id": SESSION_ID,
                "session_name": session_name,
                "created_state": create.get("state"),
                "turn1": {"status": turn1.get("status"), "marker": observed1, "turn_id": turn1.get("turn_id")},
                "turn2": {"status": turn2.get("status"), "marker": observed2, "turn_id": turn2.get("turn_id")},
                "status_ok": status.get("ok"),
                "closed_state": close.get("state"),
            },
            "business_verdict_null_all_steps": True,
        }
    finally:
        best_effort_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="smoke_codex_acpx",
        description="Real local Codex/acpx one-shot + persistent-session smoke.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Codex ACP advertised model id (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep the temp scratch/runs/sessions dirs (default: clean them up).",
    )
    parser.add_argument(
        "--acpx-timeout",
        type=int,
        default=DEFAULT_ACPX_TIMEOUT_SECONDS,
        help=f"Per-turn acpx timeout seconds (default: {DEFAULT_ACPX_TIMEOUT_SECONDS}).",
    )
    args = parser.parse_args(argv)

    try:
        env_info = preflight(model=args.model)
    except EnvironmentNotReady as exc:
        print(
            json.dumps(
                {
                    "smoke": "codex-acpx-one-shot-and-session",
                    "ok": False,
                    "skipped": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    base = Path(tempfile.mkdtemp(prefix="ars-codex-acpx-smoke-"))
    scratch = base / "scratch"
    runs_dir = base / "runs"
    sessions_dir = base / "sessions"
    exit_code = 0
    try:
        summary = run_smoke(
            model=args.model,
            acpx_timeout_seconds=args.acpx_timeout,
            scratch=scratch,
            runs_dir=runs_dir,
            sessions_dir=sessions_dir,
        )
        summary["env"] = env_info
    except SmokeError as exc:
        summary = {
            "smoke": "codex-acpx-one-shot-and-session",
            "ok": False,
            "error": str(exc),
            "env": env_info,
        }
        exit_code = 1
    except subprocess.TimeoutExpired as exc:
        summary = {
            "smoke": "codex-acpx-one-shot-and-session",
            "ok": False,
            "error": f"timeout after {exc.timeout}s running {exc.cmd[:3]!r}...",
            "env": env_info,
        }
        exit_code = 1
    finally:
        if args.keep_artifacts:
            artifacts = {
                "scratch": str(scratch),
                "runs_dir": str(runs_dir),
                "sessions_dir": str(sessions_dir),
            }
        else:
            shutil.rmtree(base, ignore_errors=True)
            artifacts = {"cleaned": True}

    summary["artifacts"] = artifacts
    print(json.dumps(summary, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Local persistent-session lifecycle smoke for S1 closure acceptance.

stdlib only. Drives a **real local** acpx persistent session through the
existing CLI surface and walks the full S1 lifecycle:

    validate-role
      -> session create
      -> session send (turn 1)
      -> session send (turn 2)
      -> session status
      -> session list
      -> session close

It uses a persistent ``AgentRoleSpec`` whose ``runner.acpx_binary`` is ``null``,
so the compiler resolves the pinned ``npx -y acpx@<acpx_version>`` prefix
(see ``policy._acpx_prefix``); `npx` is therefore a required precondition. Both prompt turns ask the
AGENT to reply with an exact marker; the smoke asserts those markers, the
``status``/``list``/``close`` outcomes, and that every lifecycle result keeps
``business_verdict: null``.

Scope boundary (S1 closure, local-only):

- It launches only an explicit local acpx/codex persistent-session smoke.
- It NEVER touches Sachima / Hermes / Gateway / IM, public ingress, real
  delivery, production config, automatic replies, or agent-to-agent routing.
- The real acpx session name is unique per run, while the local supervisor
  session id remains stable for assertions.
- Raw local smoke artifacts (temp scratch cwd, temp sessions dir, redacted turn
  artifacts) live under a private temp directory and are cleaned by default.
  Pass ``--keep-artifacts`` to retain them for inspection.

Exit codes: ``0`` smoke passed; ``1`` a lifecycle/verification step failed;
``2`` an environment precondition is missing (for this npx-pinned smoke, no ``npx``).
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

ACPX_VERSION = "0.12.0"
ADAPTER_AGENT = "codex"
MODEL = "gpt-5.5[low]"

SESSION_ID = "s1-closure-smoke"
SESSION_NAME_PREFIX = "s1-closure-smoke"

TURN1_MARKER = "S1_CLOSURE_TURN_1_OK"
TURN2_MARKER = "S1_CLOSURE_TURN_2_OK"

TURN1_PROMPT = (
    "Persistent session smoke turn 1. Do not inspect files, run tools, or edit "
    f"anything. Reply with exactly this token and nothing else: {TURN1_MARKER}"
)
TURN2_PROMPT = (
    "Persistent session smoke turn 2. Do not inspect files, run tools, or edit "
    f"anything. Reply with exactly this token and nothing else: {TURN2_MARKER}"
)

DEFAULT_ACPX_TIMEOUT_SECONDS = 240
# Extra wall-clock budget per acpx-launching CLI call to cover the one-time
# ``npx`` package fetch and process startup on top of the acpx timeout.
LAUNCH_BUFFER_SECONDS = 180
LOCAL_CALL_TIMEOUT_SECONDS = 60


class SmokeError(RuntimeError):
    """A lifecycle/verification step failed."""


class EnvironmentNotReady(RuntimeError):
    """An environment precondition (e.g. a runnable acpx) is missing."""


def build_role(scratch_cwd: Path, *, acpx_timeout_seconds: int) -> dict[str, Any]:
    """Persistent reviewer role: codex/gpt-5.5[low], npx-resolved acpx, deny-write.

    ``acpx_binary`` is ``null`` on purpose so the compiler uses the pinned
    ``npx -y acpx@<version>`` prefix. Permissions deny write/execute/terminal
    (and everything beyond read/search); persistent prompt turns additionally
    run under the fixture-proven ``--deny-all`` block.
    """
    return {
        "schema_version": 1,
        "role_id": "s1-closure-smoke-reviewer",
        "display_name": "S1 Closure Smoke",
        "description": "Local persistent-session smoke reviewer (read-only).",
        "runner": {
            "type": "acpx",
            "acpx_version": ACPX_VERSION,
            "acpx_binary": None,
            "adapter_agent": ADAPTER_AGENT,
            "model": MODEL,
        },
        "workspace": {
            "default_cwd": str(scratch_cwd),
            "allowed_roots": [str(scratch_cwd)],
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
        "session": {"strategy": "persistent"},
        "limits": {
            "timeout_seconds": acpx_timeout_seconds,
            "max_turns": 1,
            "max_output_bytes": 10_485_760,
        },
        "prompt": {
            "role_instruction": "Be brief. Reply with the requested token only.",
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
    """Return a unique acpx session name for this smoke invocation.

    acpx named sessions live outside the temp supervisor sessions directory, so
    a fixed name would let repeated or interrupted smoke runs attach to stale
    real sessions. Keep the local supervisor ``SESSION_ID`` stable for assertions,
    but isolate the real acpx session name per invocation.
    """
    return f"{SESSION_NAME_PREFIX}-{uuid.uuid4().hex[:12]}"


def _cli_env() -> dict[str, str]:
    """Inherit the ambient environment, prepending ``src`` to ``PYTHONPATH``.

    The runtime spawns acpx with this same environment, so codex auth / HOME /
    npm config must already be present in the caller's environment. No private
    runtime values are baked into this script.
    """
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(SRC_DIR), existing) if p)
    return env


def run_cli(args: list[str], *, timeout: int) -> dict[str, Any]:
    """Invoke ``python -m agent_run_supervisor`` and parse its JSON stdout line."""
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
    """Close a real acpx named session without relying on a local supervisor record.

    This is a failure-path leak guard for the create step: acpx may create the
    named session before the supervisor CLI can parse/write a local record. The
    normal smoke lifecycle still closes through the supervisor CLI; this direct
    management command is only best-effort cleanup and never masks the original
    smoke failure.
    """
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


def preflight() -> dict[str, Any]:
    """Confirm the pinned npx-based acpx path used by this smoke is runnable."""
    acpx = shutil.which("acpx")
    npx = shutil.which("npx")
    if npx is None:
        raise EnvironmentNotReady(
            "npx is required: this smoke intentionally sets runner.acpx_binary=null "
            f"so the compiler invokes 'npx -y acpx@{ACPX_VERSION}'"
        )
    return {"acpx_on_path": acpx is not None, "npx_on_path": True}


def run_smoke(*, acpx_timeout_seconds: int, scratch: Path, sessions_dir: Path) -> dict[str, Any]:
    """Drive the full local persistent-session lifecycle and verify each step."""
    session_name = make_session_name()
    work_dir = scratch / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)

    role_path = scratch / "role.json"
    role_path.write_text(
        json.dumps(build_role(work_dir, acpx_timeout_seconds=acpx_timeout_seconds), indent=2),
        encoding="utf-8",
    )
    turn1_prompt = scratch / "turn1.prompt.txt"
    turn1_prompt.write_text(TURN1_PROMPT, encoding="utf-8")
    turn2_prompt = scratch / "turn2.prompt.txt"
    turn2_prompt.write_text(TURN2_PROMPT, encoding="utf-8")

    launch_timeout = acpx_timeout_seconds + LAUNCH_BUFFER_SECONDS
    sessions_arg = ["--sessions-dir", str(sessions_dir)]
    common = ["--role", str(role_path), "--session-id", SESSION_ID]

    created = False
    closed = False
    create_attempted = False

    def best_effort_close() -> None:
        """Avoid leaking a real acpx persistent session if a later check fails."""
        if closed:
            return
        if created:
            try:
                cleanup = run_cli(
                    ["session", "close", *common, *sessions_arg], timeout=launch_timeout
                )
                if cleanup.get("returncode") == 0:
                    return
            except Exception:
                # Fall through to direct named-session cleanup below.
                pass
        if create_attempted:
            best_effort_close_acpx_session(
                session_name=session_name,
                cwd=work_dir,
                timeout=launch_timeout,
            )

    try:
        # 1. validate-role -------------------------------------------------
        res = run_cli(["validate-role", str(role_path)], timeout=LOCAL_CALL_TIMEOUT_SECONDS)
        _require(res["returncode"] == 0, f"validate-role: exit {res['returncode']}: {res['stderr'].strip()!r}")
        validate = _require_json(res, "validate-role")
        _require(validate.get("valid") is True, "validate-role: role not reported valid")

        # 2. session create ------------------------------------------------
        create_attempted = True
        res = run_cli(
            ["session", "create", *common, "--session-name", session_name, *sessions_arg],
            timeout=launch_timeout,
        )
        _require(res["returncode"] == 0, f"session create: exit {res['returncode']}: {res['stderr'].strip()!r}")
        # Once the create command exits 0, assume a real acpx named session may
        # exist even if the following JSON/field validation fails. This makes the
        # finally block close the session on post-create validation failures too.
        created = True
        create = _require_json(res, "session create")
        _require(create.get("state") == "open", f"session create: state={create.get('state')!r}, want 'open'")
        _check_business_verdict_null(create, "session create")

        # 3. session send (turn 1) ----------------------------------------
        res = run_cli(
            ["session", "send", *common, "--prompt-file", str(turn1_prompt), *sessions_arg],
            timeout=launch_timeout,
        )
        _require(res["returncode"] == 0, f"session send #1: exit {res['returncode']}: {res['stderr'].strip()!r}")
        turn1 = _require_json(res, "session send #1")
        _require(turn1.get("status") == "completed", f"session send #1: status={turn1.get('status')!r}")
        observed1 = (turn1.get("final_message") or "").strip()
        _require(observed1 == TURN1_MARKER, f"session send #1: final_message={observed1!r}, want {TURN1_MARKER!r}")
        _check_business_verdict_null(turn1, "session send #1")

        # 4. session send (turn 2) ----------------------------------------
        res = run_cli(
            ["session", "send", *common, "--prompt-file", str(turn2_prompt), *sessions_arg],
            timeout=launch_timeout,
        )
        _require(res["returncode"] == 0, f"session send #2: exit {res['returncode']}: {res['stderr'].strip()!r}")
        turn2 = _require_json(res, "session send #2")
        _require(turn2.get("status") == "completed", f"session send #2: status={turn2.get('status')!r}")
        observed2 = (turn2.get("final_message") or "").strip()
        _require(observed2 == TURN2_MARKER, f"session send #2: final_message={observed2!r}, want {TURN2_MARKER!r}")
        _check_business_verdict_null(turn2, "session send #2")
        _require(
            turn1.get("turn_id") != turn2.get("turn_id"),
            "session send: the two turns must have distinct turn ids",
        )

        # 5. session status ------------------------------------------------
        res = run_cli(["session", "status", *common, *sessions_arg], timeout=launch_timeout)
        _require(res["returncode"] == 0, f"session status: exit {res['returncode']}: {res['stderr'].strip()!r}")
        status = _require_json(res, "session status")
        _require(status.get("ok") is True, f"session status: ok={status.get('ok')!r}")
        _check_business_verdict_null(status, "session status")

        # 6. session list --------------------------------------------------
        res = run_cli(
            ["session", "list", "--role", str(role_path), *sessions_arg],
            timeout=LOCAL_CALL_TIMEOUT_SECONDS,
        )
        _require(res["returncode"] == 0, f"session list: exit {res['returncode']}: {res['stderr'].strip()!r}")
        listing = _require_json(res, "session list")
        _require(listing.get("count") == 1, f"session list: count={listing.get('count')!r}, want 1")
        records = listing.get("sessions") or []
        _require(len(records) == 1, f"session list: {len(records)} records, want 1")
        _require(records[0].get("session_id") == SESSION_ID, "session list: unexpected session_id")
        _check_business_verdict_null(listing, "session list")

        # 7. session close -------------------------------------------------
        res = run_cli(["session", "close", *common, *sessions_arg], timeout=launch_timeout)
        _require(res["returncode"] == 0, f"session close: exit {res['returncode']}: {res['stderr'].strip()!r}")
        close = _require_json(res, "session close")
        _require(close.get("state") == "closed", f"session close: state={close.get('state')!r}, want 'closed'")
        _require(close.get("closed") is True, "session close: closed flag not true")
        _check_business_verdict_null(close, "session close")
        closed = True

        # Two distinct redacted turn directories persisted under the one session.
        turns_root = sessions_dir / SESSION_ID / "turns"
        turn_dirs = sorted(p.name for p in turns_root.iterdir() if p.is_dir()) if turns_root.exists() else []
        _require(len(turn_dirs) == 2, f"expected 2 turn dirs under {turns_root}, found {len(turn_dirs)}")

        return {
            "smoke": "persistent-session-lifecycle",
            "ok": True,
            "acpx_version": ACPX_VERSION,
            "adapter_agent": ADAPTER_AGENT,
            "model": MODEL,
            "session_id": SESSION_ID,
            "session_name": session_name,
            "acpx_session_id": create.get("acpx_session_id"),
            "lifecycle": {
                "validate_role_valid": True,
                "created_state": create.get("state"),
                "turn1": {"status": turn1.get("status"), "marker": observed1, "turn_id": turn1.get("turn_id")},
                "turn2": {"status": turn2.get("status"), "marker": observed2, "turn_id": turn2.get("turn_id")},
                "status_ok": status.get("ok"),
                "list_count": listing.get("count"),
                "closed_state": close.get("state"),
            },
            "turn_dir_count": len(turn_dirs),
            "business_verdict_null_all_steps": True,
        }
    finally:
        best_effort_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="smoke_persistent_session",
        description="Real local acpx persistent-session lifecycle smoke (S1 closure).",
    )
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep the temp scratch/sessions dirs (default: clean them up).",
    )
    parser.add_argument(
        "--acpx-timeout",
        type=int,
        default=DEFAULT_ACPX_TIMEOUT_SECONDS,
        help=f"Per-turn acpx timeout seconds (default: {DEFAULT_ACPX_TIMEOUT_SECONDS}).",
    )
    args = parser.parse_args(argv)

    try:
        env_info = preflight()
    except EnvironmentNotReady as exc:
        print(json.dumps({"smoke": "persistent-session-lifecycle", "ok": False, "skipped": str(exc)}))
        return 2

    base = Path(tempfile.mkdtemp(prefix="ars-s1-closure-smoke-"))
    scratch = base / "scratch"
    sessions_dir = base / "sessions"
    summary: dict[str, Any] = {"smoke": "persistent-session-lifecycle", "ok": False}
    exit_code = 0
    try:
        summary = run_smoke(
            acpx_timeout_seconds=args.acpx_timeout,
            scratch=scratch,
            sessions_dir=sessions_dir,
        )
        summary["env"] = env_info
    except SmokeError as exc:
        summary = {"smoke": "persistent-session-lifecycle", "ok": False, "error": str(exc), "env": env_info}
        exit_code = 1
    except subprocess.TimeoutExpired as exc:
        summary = {
            "smoke": "persistent-session-lifecycle",
            "ok": False,
            "error": f"timeout after {exc.timeout}s running {exc.cmd[:3]!r}...",
            "env": env_info,
        }
        exit_code = 1
    finally:
        if args.keep_artifacts:
            summary_artifacts = {"scratch": str(scratch), "sessions_dir": str(sessions_dir)}
        else:
            shutil.rmtree(base, ignore_errors=True)
            summary_artifacts = {"cleaned": True}

    summary["artifacts"] = summary_artifacts
    print(json.dumps(summary, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

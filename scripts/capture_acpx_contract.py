from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO / "fixtures" / "acpx-0.10.0"
SCRATCH_ROOT = REPO / ".tmp" / "acpx-contract-scratch"
ACPX = ["npx", "-y", "acpx@0.10.0"]
COMMON_FLAGS = [
    "--format", "json",
    "--json-strict",
    "--suppress-reads",
    "--timeout", "180",
    "--max-turns", "1",
]
CODEX_ENV = {
    "HOME": "/home/ecs-user",
    "CODEX_PATH": "/home/ecs-user/.local/bin/codex",
    "npm_config_update_notifier": "false",
}


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    argv: list[str]
    expected_exit: int
    stdout_name: str = "stdout.ndjson"
    timeout_seconds: int = 240
    description: str = ""


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(argv: list[str], *, cwd: Path | None = None, timeout: int = 60) -> tuple[int, str, str]:
    env = os.environ.copy()
    env.update(CODEX_ENV)
    completed = subprocess.run(
        argv,
        cwd=str(cwd or REPO),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def init_scratch(name: str) -> Path:
    scratch = SCRATCH_ROOT / name
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=scratch, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    (scratch / "README.md").write_text(f"{name} sentinel file\n", encoding="utf-8")
    return scratch


def run_fixture(spec: FixtureSpec) -> dict[str, Any]:
    fixture_dir = FIXTURE_ROOT / spec.name
    if fixture_dir.exists():
        shutil.rmtree(fixture_dir)
    fixture_dir.mkdir(parents=True)

    started = iso_now()
    start = time.monotonic()
    exit_code, stdout, stderr = run(spec.argv, timeout=spec.timeout_seconds)
    duration = time.monotonic() - start
    ended = iso_now()

    (fixture_dir / spec.stdout_name).write_text(stdout, encoding="utf-8")
    (fixture_dir / "stderr.log").write_text(stderr, encoding="utf-8")
    write_json(fixture_dir / "command.argv.json", spec.argv)
    metadata = {
        "name": spec.name,
        "description": spec.description,
        "started_at": started,
        "ended_at": ended,
        "duration_seconds": round(duration, 3),
        "expected_exit": spec.expected_exit,
        "stdout_file": spec.stdout_name,
        "stderr_file": "stderr.log",
        "runner_flags_family": COMMON_FLAGS,
    }
    write_json(fixture_dir / "metadata.json", metadata)
    result = {
        "name": spec.name,
        "exit_code": exit_code,
        "expected_exit": spec.expected_exit,
        "matches_expected_exit": exit_code == spec.expected_exit,
        "stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
        "stdout_lines": len(stdout.splitlines()),
        "stderr_lines": len(stderr.splitlines()),
    }
    write_json(fixture_dir / "result.json", result)
    return result


def parse_ndjson_lines(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def schema_summary() -> dict[str, Any]:
    success = parse_ndjson_lines(FIXTURE_ROOT / "success-codex-sentinel" / "stdout.ndjson")
    methods = sorted({str(record.get("method")) for record in success if "method" in record})
    session_updates = sorted({
        str(record.get("params", {}).get("update", {}).get("sessionUpdate"))
        for record in success
        if isinstance(record.get("params"), dict) and isinstance(record.get("params", {}).get("update"), dict)
    })
    return {
        "stdout_shape": "newline-delimited JSON objects observed from acpx --format json",
        "jsonrpc_present": any(record.get("jsonrpc") == "2.0" for record in success),
        "methods": methods,
        "session_update_types": session_updates,
        "line_count": len(success),
    }


def main() -> int:
    if FIXTURE_ROOT.exists():
        shutil.rmtree(FIXTURE_ROOT)
    FIXTURE_ROOT.mkdir(parents=True)
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)

    success_scratch = init_scratch("success-codex-sentinel")
    permission_scratch = init_scratch("permission-denied-codex-read")
    no_session_scratch = init_scratch("management-no-session")
    timeout_scratch = init_scratch("timeout-hanging-agent")
    runtime_scratch = init_scratch("runtime-error-agent")

    version_exit, version_stdout, version_stderr = run(ACPX + ["--version"], timeout=120)
    node_exit, node_stdout, node_stderr = run(["node", "--version"], timeout=30)
    npm_exit, npm_stdout, npm_stderr = run(["npm", "--version"], timeout=30)
    codex_status_exit, codex_status_stdout, codex_status_stderr = run(
        ACPX + ["--format", "json", "--json-strict", "--cwd", str(success_scratch), "codex", "status"],
        timeout=120,
    )

    fixtures = [
        FixtureSpec(
            name="success-codex-sentinel",
            expected_exit=0,
            description="No-tool Codex connectivity sentinel using exact V0.1a runner flag family.",
            argv=ACPX + COMMON_FLAGS + [
                "--cwd", str(success_scratch),
                "--deny-all",
                "--non-interactive-permissions", "fail",
                "--no-terminal",
                "--model", "gpt-5.5[low]",
                "codex", "exec",
                "Connectivity smoke only. Do not inspect files, do not run tools, do not edit files. Reply exactly: CODEX_ACPX_OK",
            ],
        ),
        FixtureSpec(
            name="usage-error-invalid-flag",
            expected_exit=2,
            description="Invalid CLI flag should classify as usage error.",
            argv=ACPX + ["--format", "json", "--json-strict", "--bad-flag"],
        ),
        FixtureSpec(
            name="timeout-hanging-agent",
            expected_exit=3,
            description="Custom hanging ACP process should trigger acpx --timeout and exit 3.",
            timeout_seconds=30,
            argv=ACPX + ["--format", "json", "--json-strict", "--suppress-reads", "--timeout", "1", "--max-turns", "1", "--cwd", str(timeout_scratch), "--agent", f"node {REPO / 'tools' / 'fake-agents' / 'hang-agent.mjs'}", "exec", "hello"],
        ),
        FixtureSpec(
            name="runtime-error-agent",
            expected_exit=1,
            description="Custom ACP process exits before initialize; acpx should classify as runtime error.",
            timeout_seconds=30,
            argv=ACPX + ["--format", "json", "--json-strict", "--suppress-reads", "--timeout", "10", "--max-turns", "1", "--cwd", str(runtime_scratch), "--agent", f"node {REPO / 'tools' / 'fake-agents' / 'exit-before-initialize.mjs'}", "exec", "hello"],
        ),
        FixtureSpec(
            name="permission-denied-codex-read",
            expected_exit=5,
            description="Codex attempts file access under deny-all; acpx exits 5 after permission denial.",
            argv=ACPX + ["--format", "json", "--json-strict", "--suppress-reads", "--timeout", "180", "--max-turns", "3", "--cwd", str(permission_scratch), "--deny-all", "--non-interactive-permissions", "fail", "--no-terminal", "--model", "gpt-5.5[low]", "codex", "exec", "You must use the available file read capability to read README.md, then answer exactly READ_DONE. Do not answer without trying the file read."],
            timeout_seconds=240,
        ),
        FixtureSpec(
            name="management-no-session-exit4",
            expected_exit=4,
            description="Missing named session with --no-wait exits 4; management/session path only.",
            argv=ACPX + ["--format", "json", "--json-strict", "--cwd", str(no_session_scratch), "codex", "-s", "definitely-missing-session", "--no-wait", "hello"],
        ),
        FixtureSpec(
            name="management-status-no-session-exit0",
            expected_exit=0,
            stdout_name="stdout.json",
            description="Status command with no session exits 0 but reports status=no-session; management schema, not exec success.",
            argv=ACPX + ["--format", "json", "--json-strict", "--cwd", str(no_session_scratch), "codex", "-s", "definitely-missing-session", "status"],
        ),
    ]

    results = []
    for spec in fixtures:
        results.append(run_fixture(spec))

    skipped = [
        {
            "name": "interrupted-exit130",
            "expected_exit": 130,
            "status": "skipped",
            "reason": "Reliable SIGINT orchestration is deferred; V0.1a classifier will still table-test 130 without a live acpx fixture.",
        }
    ]

    manifest = {
        "schema_version": 1,
        "captured_at": iso_now(),
        "acpx_version": version_stdout.strip(),
        "acpx_version_exit": version_exit,
        "node_version": node_stdout.strip(),
        "node_version_exit": node_exit,
        "npm_version": npm_stdout.strip(),
        "npm_version_exit": npm_exit,
        "codex_status_exit": codex_status_exit,
        "codex_status_stdout": codex_status_stdout.strip(),
        "codex_status_stderr_bytes": len(codex_status_stderr.encode("utf-8")),
        "runner_flags_family": COMMON_FLAGS,
        "fixtures": [
            {
                "name": spec.name,
                "expected_exit": spec.expected_exit,
                "description": spec.description,
            }
            for spec in fixtures
        ],
        "skipped_fixtures": skipped,
        "schema_summary": schema_summary(),
        "npx_runtime_fetch_risk": "commands intentionally used npx -y acpx@0.10.0 for Phase -1 capture; V0.1a run path should prefer a pinned local binary/digest.",
    }
    write_json(FIXTURE_ROOT / "manifest.json", manifest)

    readme = f"""# acpx@0.10.0 Contract Fixtures

Captured: {manifest['captured_at']}

## Versions

- acpx: `{manifest['acpx_version']}`
- node: `{manifest['node_version']}`
- npm: `{manifest['npm_version']}`

## Runner flag family

```text
{' '.join(COMMON_FLAGS)}
```

## AcpxStdoutSchema

Observed stdout for `--format json --json-strict --suppress-reads` is newline-delimited JSON. The success fixture reports:

```json
{json.dumps(manifest['schema_summary'], ensure_ascii=False, indent=2)}
```

The V0.1a parser must target this observed stdout schema. Management-command JSON such as `status=no-session` is stored separately and must not be parsed as exec success.

## Fixtures

"""
    for result in results:
        readme += f"- `{result['name']}`: exit `{result['exit_code']}` expected `{result['expected_exit']}`, stdout lines `{result['stdout_lines']}`.\n"
    readme += "\n## Skipped\n\n- `interrupted-exit130`: skipped for live acpx capture; classifier should still table-test exit 130.\n"
    (FIXTURE_ROOT / "README.md").write_text(readme, encoding="utf-8")

    all_ok = all(result["matches_expected_exit"] for result in results)
    print(json.dumps({"fixture_root": str(FIXTURE_ROOT), "all_expected_exits_matched": all_ok, "fixtures": results}, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

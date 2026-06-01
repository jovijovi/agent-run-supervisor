"""W3 fidelity guard for ``docs/design/result-event-schema.md``.

This test pins the *documented* top-level ``result.json`` key set against the
keys ``result.build_result_payload`` actually emits, so the caller-stable schema
doc can never silently drift from the code. The doc embeds its canonical key
table between machine-readable markers; this test parses that table and compares
it to ``set(build_result_payload(...).keys())``.
"""
from __future__ import annotations

import re
from pathlib import Path

from agent_run_supervisor.exit_classifier import AgentRunStatus
from agent_run_supervisor.result import build_result_payload

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_DOC = _REPO_ROOT / "docs" / "design" / "result-event-schema.md"

_KEYS_BEGIN = "<!-- result-json-keys:begin -->"
_KEYS_END = "<!-- result-json-keys:end -->"
# A documented key is the backtick-wrapped token in the first column of a table
# row inside the marked block, e.g. ``| `run_id` | string | ... |``.
_FIRST_COLUMN_KEY = re.compile(r"^\|\s*`([^`]+)`\s*\|")


def _documented_result_keys() -> set[str]:
    text = _SCHEMA_DOC.read_text(encoding="utf-8")
    start = text.index(_KEYS_BEGIN) + len(_KEYS_BEGIN)
    end = text.index(_KEYS_END, start)
    keys: set[str] = set()
    for line in text[start:end].splitlines():
        match = _FIRST_COLUMN_KEY.match(line)
        if match:
            keys.add(match.group(1))
    return keys


def _actual_result_keys() -> set[str]:
    payload = build_result_payload(
        run_id="run_probe",
        status=AgentRunStatus.COMPLETED,
        origin="cli",
        detail_code=None,
        retryable=False,
        exit_code=0,
        signal=None,
        stop_reason="end_turn",
        usage=None,
        final_message="",
        truncated=False,
        truncate_reason=None,
        run_dir=Path("/tmp/run_probe"),
    )
    return set(payload.keys())


def test_schema_doc_exists_with_frontmatter() -> None:
    assert _SCHEMA_DOC.is_file(), f"missing schema doc: {_SCHEMA_DOC}"
    text = _SCHEMA_DOC.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "schema doc must open with a YAML frontmatter block"


def test_documented_result_keys_match_build_result_payload() -> None:
    documented = _documented_result_keys()
    actual = _actual_result_keys()
    assert documented, "no result.json keys parsed from the schema doc markers"
    assert documented == actual, (
        "result.json schema doc drifted from build_result_payload(): "
        f"missing_from_doc={sorted(actual - documented)} "
        f"extra_in_doc={sorted(documented - actual)}"
    )

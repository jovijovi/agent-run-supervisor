from __future__ import annotations

from pathlib import Path

from scripts.validate_contract_fixtures import find_secret_like_values, parse_json_lines


def test_parse_json_lines_rejects_malformed_line(tmp_path: Path) -> None:
    fixture = tmp_path / "stdout.ndjson"
    fixture.write_text('{"ok": true}\nnot-json\n', encoding="utf-8")

    try:
        parse_json_lines(fixture)
    except ValueError as exc:
        assert "line 2" in str(exc)
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("malformed NDJSON should fail closed")


def test_secret_scan_detects_common_token_shapes(tmp_path: Path) -> None:
    fixture = tmp_path / "stderr.log"
    fixture.write_text("Authorization: " + "Bearer " + "sk-" + "test_should_not_be_here", encoding="utf-8")

    findings = find_secret_like_values(tmp_path)

    assert findings
    assert findings[0].path == fixture
    assert "Bearer" in findings[0].pattern_name

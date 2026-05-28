from __future__ import annotations

from agent_run_supervisor.redaction import (
    RedactionReport,
    redact_argv,
    redact_env,
    redact_text,
    redact_mapping,
)

SYNTHETIC_OPENAI_KEY = "sk-" + "ABC" + "123" * 6
SYNTHETIC_JWT = "eyJ" + "ABCabc" + ".eyJ" + "DEFdef" + ".sig" + "XYZ_xyz_-987654"
SYNTHETIC_BEARER = "Authorization: " + "Bearer " + "abcdef" + "012345"


def test_redact_text_replaces_openai_key_shape() -> None:
    redacted, report = redact_text(f"loaded {SYNTHETIC_OPENAI_KEY} from env")

    assert SYNTHETIC_OPENAI_KEY not in redacted
    assert "[REDACTED" in redacted
    assert any(item.pattern_name.lower().startswith("openai") for item in report.matches)


def test_redact_text_replaces_jwt_and_bearer_shapes() -> None:
    text = f"{SYNTHETIC_JWT} and {SYNTHETIC_BEARER}"
    redacted, report = redact_text(text)

    assert SYNTHETIC_JWT not in redacted
    assert SYNTHETIC_BEARER.split(" ", 1)[1].split(" ", 1)[1] not in redacted
    pattern_names = {item.pattern_name.lower() for item in report.matches}
    assert "jwt" in pattern_names
    assert any("bearer" in name for name in pattern_names)


def test_redact_env_drops_sensitive_keys_and_redacts_values() -> None:
    env = {
        "OPENAI_API_KEY": SYNTHETIC_OPENAI_KEY,
        "PATH": "/usr/local/bin",
        "GENERIC_TOKEN": "do_not_keep_me",
    }

    redacted_env, report = redact_env(env)

    assert "[REDACTED]" == redacted_env["OPENAI_API_KEY"]
    assert "[REDACTED]" == redacted_env["GENERIC_TOKEN"]
    assert redacted_env["PATH"] == "/usr/local/bin"
    assert any("OPENAI_API_KEY" in item.note for item in report.matches)


def test_redact_argv_masks_inline_secrets() -> None:
    argv = [
        "acpx",
        "--api-key",
        SYNTHETIC_OPENAI_KEY,
        "--cwd",
        "/tmp/work",
    ]

    redacted, report = redact_argv(argv)

    assert SYNTHETIC_OPENAI_KEY not in redacted
    assert "[REDACTED]" in redacted
    assert report.matches


def test_redact_mapping_redacts_strings_recursively() -> None:
    mapping = {
        "prompt": f"My key is {SYNTHETIC_OPENAI_KEY}.",
        "nested": {"jwt": SYNTHETIC_JWT, "ok": "safe value"},
    }

    redacted, report = redact_mapping(mapping)

    assert SYNTHETIC_OPENAI_KEY not in str(redacted)
    assert SYNTHETIC_JWT not in str(redacted)
    assert redacted["nested"]["ok"] == "safe value"
    assert report.matches

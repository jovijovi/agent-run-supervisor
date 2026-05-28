from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

REDACTED_PLACEHOLDER = "[REDACTED]"
REDACTED_INLINE = "[REDACTED]"

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")),
    ("bearer_token", re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+[^\s]+")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")),
    ("pem_private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

# Env keys that should be dropped/redacted regardless of value shape.
_SENSITIVE_ENV_SUBSTRINGS: tuple[str, ...] = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "API_KEY",
    "PRIVATE_KEY",
    "CREDENTIAL",
    "OPENAI",
    "ANTHROPIC",
)


@dataclass(frozen=True)
class RedactionMatch:
    pattern_name: str
    note: str = ""


@dataclass
class RedactionReport:
    matches: list[RedactionMatch] = field(default_factory=list)

    def merge(self, other: "RedactionReport") -> None:
        self.matches.extend(other.matches)


def _redact_with_patterns(value: str, report: RedactionReport, location: str) -> str:
    redacted = value
    for name, pattern in _PATTERNS:
        if pattern.search(redacted):
            redacted = pattern.sub(REDACTED_INLINE, redacted)
            report.matches.append(RedactionMatch(pattern_name=name, note=location))
    return redacted


def redact_text(value: str, *, location: str = "text") -> tuple[str, RedactionReport]:
    report = RedactionReport()
    return _redact_with_patterns(value, report, location), report


def _is_sensitive_env_key(name: str) -> bool:
    upper = name.upper()
    return any(token in upper for token in _SENSITIVE_ENV_SUBSTRINGS)


def redact_env(env: Mapping[str, str]) -> tuple[dict[str, str], RedactionReport]:
    report = RedactionReport()
    redacted: dict[str, str] = {}
    for name, value in env.items():
        if _is_sensitive_env_key(name):
            redacted[name] = REDACTED_PLACEHOLDER
            report.matches.append(
                RedactionMatch(pattern_name="env_sensitive_key", note=name),
            )
            continue
        if isinstance(value, str):
            new_value = _redact_with_patterns(value, report, f"env:{name}")
            redacted[name] = new_value
        else:
            redacted[name] = str(value)
    return redacted, report


def redact_argv(argv: Sequence[str]) -> tuple[list[str], RedactionReport]:
    report = RedactionReport()
    redacted: list[str] = []
    sensitive_prefixes = ("--api-key", "--token", "--password")
    redact_next = False
    for arg in argv:
        if redact_next:
            redacted.append(REDACTED_PLACEHOLDER)
            report.matches.append(
                RedactionMatch(pattern_name="argv_sensitive_flag", note=str(arg)),
            )
            redact_next = False
            continue
        if isinstance(arg, str) and arg in sensitive_prefixes:
            redacted.append(arg)
            redact_next = True
            continue
        if isinstance(arg, str):
            redacted.append(_redact_with_patterns(arg, report, "argv"))
        else:
            redacted.append(str(arg))
    return redacted, report


def redact_mapping(mapping: Mapping[str, Any]) -> tuple[dict[str, Any], RedactionReport]:
    report = RedactionReport()
    return _redact_value(mapping, report, "$"), report


def _redact_value(value: Any, report: RedactionReport, location: str) -> Any:
    if isinstance(value, str):
        return _redact_with_patterns(value, report, location)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, sub in value.items():
            sub_location = f"{location}.{key}"
            if isinstance(key, str) and _is_sensitive_env_key(key):
                result[key] = REDACTED_PLACEHOLDER
                report.matches.append(
                    RedactionMatch(
                        pattern_name="mapping_sensitive_key",
                        note=sub_location,
                    )
                )
                continue
            result[key] = _redact_value(sub, report, sub_location)
        return result
    if isinstance(value, list):
        return [
            _redact_value(item, report, f"{location}[{index}]")
            for index, item in enumerate(value)
        ]
    return value

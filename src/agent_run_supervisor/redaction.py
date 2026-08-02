from __future__ import annotations

import os
import re
from dataclasses import InitVar, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

REDACTED_PLACEHOLDER = "[REDACTED]"
REDACTED_INLINE = "[REDACTED]"

# -- V4 §6.3 environment-value sink boundary --------------------------------
#
# Fixed source literals. None of them is derived from, sized by, or otherwise
# computed over an input: a replacement that carried input data would itself be
# a projection of the value it replaced.
ENV_VALUE_REPLACEMENT = "[ENV_VALUE_REDACTED]"
ENV_VALUE_REPLACEMENT_BYTES = b"[ENV_VALUE_REDACTED]"
GUARDED_TEXT_WITHHELD = "[GUARDED_TEXT_WITHHELD]"
GUARDED_TEXT_WITHHELD_BYTES = b"[GUARDED_TEXT_WITHHELD]"
GUARDED_VALUE_WITHHELD = "[GUARDED_VALUE_WITHHELD]"
GUARDED_RECORD_WITHHELD = "GUARDED_RECORD_WITHHELD"

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
    pending_flag = ""
    for arg in argv:
        if redact_next:
            redacted.append(REDACTED_PLACEHOLDER)
            # Reviewer note 6: the note names the *flag* that caused the
            # redaction, never the redacted argument. Recording the value here
            # re-published the exact material this function exists to remove.
            report.matches.append(
                RedactionMatch(
                    pattern_name="argv_sensitive_flag",
                    note=f"argv:{pending_flag}",
                ),
            )
            redact_next = False
            pending_flag = ""
            continue
        if isinstance(arg, str) and arg in sensitive_prefixes:
            redacted.append(arg)
            redact_next = True
            pending_flag = arg
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


# ---------------------------------------------------------------------------
# RunTextGuard — the ephemeral per-Run environment-value literal guard
# ---------------------------------------------------------------------------


# Held only by this module. A type barrier a caller can simply construct is
# not a barrier, so minting is what the seams actually trust.
_SAFE_TEXT_MINT = object()


@dataclass(frozen=True)
class SafeText:
    """A free-form text projection that has crossed :class:`RunTextGuard`.

    The type — not a comment — is what the durable free-form seams accept, so
    an unguarded ``str`` cannot reach them by an ordinary call. Construction
    requires the module-private mint, which means the shortest path around the
    guard is closed: ``SafeText(raw_child_text)`` raises rather than producing
    the exact type every seam checks for.

    This is a structural *ordinary-call* seam, not an in-process sandbox
    claim. Arbitrary reflection — ``object.__new__``, rebinding the mint, or
    rewriting this module — is outside what Python can defend and outside what
    ARS claims to defend.
    """

    text: str
    mint: InitVar[object] = None

    def __post_init__(self, mint: object) -> None:
        if mint is not _SAFE_TEXT_MINT:
            raise TypeError(
                "SafeText is minted only by RunTextGuard.safe_text(); an "
                "ordinary constructor call would defeat the seam it exists "
                "to be"
            )

    def __str__(self) -> str:
        return self.text


# The one safe projection that needs no guard: an empty string carries no
# value, so it is source-owned rather than guard-produced.
EMPTY_SAFE_TEXT = SafeText("", _SAFE_TEXT_MINT)


@dataclass
class GuardCounters:
    """Coarse, sink-local integers — never a length, prefix, or suffix.

    Recording how much text was replaced would be a length-by-value; recording
    only *that* something was replaced keeps the erasure measurable without
    describing what was erased.
    """

    matched_occurrences: int = 0
    suppressed_fields: int = 0
    suppressed_records: int = 0


class _WithheldRecord:
    """Internal marker: this mapping cannot be projected safely at all."""

    __slots__ = ()


_WITHHELD_RECORD = _WithheldRecord()


def _claim_spans(haystack: Any, literals: Sequence[Any]) -> list[tuple[int, int]]:
    """Non-overlapping occurrence spans, longest literal first.

    ``literals`` arrives sorted longest-first, so a long value always claims
    its span before a short one can fragment it. An occurrence overlapping an
    already-claimed span is skipped rather than claimed: the overlap already
    removes at least one of its own characters, so nothing survives whole.

    Works identically over ``str`` and ``bytes`` because both expose ``find``;
    there is no regex, no compiled-pattern cache keyed by a value, and no
    membership test that would hash one.
    """
    spans: list[tuple[int, int]] = []
    for literal in literals:
        width = len(literal)
        if width == 0:
            continue
        start = 0
        while True:
            index = haystack.find(literal, start)
            if index < 0:
                break
            end = index + width
            if _span_is_free(spans, index, end):
                spans.append((index, end))
                start = end
            else:
                start = index + 1
    spans.sort()
    return spans


def _span_is_free(spans: Sequence[tuple[int, int]], start: int, end: int) -> bool:
    for claimed_start, claimed_end in spans:
        if start < claimed_end and claimed_start < end:
            return False
    return True


def _rebuild(haystack: Any, spans: Sequence[tuple[int, int]], token: Any, empty: Any):
    if not spans:
        return haystack
    parts = []
    position = 0
    for start, end in spans:
        parts.append(haystack[position:start])
        parts.append(token)
        position = end
    parts.append(haystack[position:])
    return empty.join(parts)


class RunTextGuard:
    """Per-Run exact-literal guard over the final projected environment values.

    Every non-empty final value — base, pass-through, overlay, and mediation
    alike — is a literal here, because V4 treats every environment value as
    sensitive regardless of key name, source class, length, or shape.

    The sensitive set lives in memory only, as a plain tuple. It is never a
    ``set``/``dict`` key, a compiled-regex cache key, a Bloom filter, a digest,
    or any other structure that would hash it; it has no serializer, no
    value-derived equality, and no accessor that enumerates it. Matching is a
    bounded direct ``find`` walk in both directions (text and ``os.fsencode``
    exec bytes).

    Lifetime is minimized, not zeroed: :meth:`clear` drops the tuple once the
    Run's last sink has been served. Python cannot erase an immutable string
    and this class never claims otherwise.
    """

    __slots__ = (
        "_literals",
        "_byte_literals",
        "_max_literal_chars",
        "_max_literal_bytes",
        "counters",
    )

    def __init__(self, literals: Iterable[str]) -> None:
        deduped: list[str] = []
        for candidate in literals:
            if not isinstance(candidate, str) or candidate == "":
                continue
            # Direct equality, never set membership: a set would hash it.
            if not any(candidate == existing for existing in deduped):
                deduped.append(candidate)
        deduped.sort(key=len, reverse=True)
        byte_deduped: list[bytes] = []
        for value in deduped:
            encoded = os.fsencode(value)
            if not any(encoded == existing for existing in byte_deduped):
                byte_deduped.append(encoded)
        byte_deduped.sort(key=len, reverse=True)
        self._literals: tuple[str, ...] = tuple(deduped)
        self._byte_literals: tuple[bytes, ...] = tuple(byte_deduped)
        self._max_literal_chars = max((len(item) for item in deduped), default=0)
        self._max_literal_bytes = max((len(item) for item in byte_deduped), default=0)
        self.counters = GuardCounters()

    @classmethod
    def from_environment(cls, env: Any) -> "RunTextGuard":
        """Build the guard from the final per-Run environment after precedence.

        Accepts the ephemeral ``ResolvedEnvironment`` carrier — the same object
        the spawn seam receives, so the guard's literal set can never disagree
        with what the child was handed — or a plain mapping for the direct
        test/dev path.

        Empty strings contribute no bytes: an empty literal would match at
        every position and turn every sink into a withholding marker.
        """
        sensitive = getattr(env, "sensitive_values", None)
        if callable(sensitive):
            return cls(sensitive())
        return cls(env.values())

    # -- introspection that is safe by construction ------------------------

    @property
    def max_literal_chars(self) -> int:
        return self._max_literal_chars

    @property
    def max_literal_bytes(self) -> int:
        return self._max_literal_bytes

    def report(self) -> dict[str, int]:
        return {
            "matched_occurrences": self.counters.matched_occurrences,
            "suppressed_fields": self.counters.suppressed_fields,
            "suppressed_records": self.counters.suppressed_records,
        }

    def clear(self) -> None:
        self._literals = ()
        self._byte_literals = ()
        self._max_literal_chars = 0
        self._max_literal_bytes = 0

    # -- matchers ----------------------------------------------------------

    def matches(self, value: str) -> bool:
        if not isinstance(value, str):
            return False
        for literal in self._literals:
            if literal in value:
                return True
        return False

    def matches_bytes(self, value: bytes) -> bool:
        if not isinstance(value, (bytes, bytearray)):
            return False
        raw = bytes(value)
        for literal in self._byte_literals:
            if literal in raw:
                return True
        return False

    # -- text / bytes ------------------------------------------------------

    def guard_text(self, value: str) -> str:
        """Replace every literal occurrence, or withhold the whole field.

        The postcondition rescan is not decoration: replacement inserts a fixed
        token, and a value that happens to be a substring of that token would
        otherwise reappear in the output. When safe replacement cannot be
        established the field is suppressed categorically — confidentiality
        wins over evidence completeness, with no minimum length and no
        inconvenience waiver.
        """
        if not isinstance(value, str):
            self.counters.suppressed_fields += 1
            return GUARDED_VALUE_WITHHELD
        if not self._literals or not value:
            return value
        spans = _claim_spans(value, self._literals)
        if not spans:
            return value
        self.counters.matched_occurrences += len(spans)
        guarded = _rebuild(value, spans, ENV_VALUE_REPLACEMENT, "")
        if self.matches(guarded):
            self.counters.suppressed_fields += 1
            return GUARDED_TEXT_WITHHELD
        return guarded

    def guard_bytes(self, value: bytes) -> bytes:
        """The exec-byte matcher, applied *before* any decode.

        A value that survives ``os.fsencode`` round-tripping but not UTF-8
        decoding would otherwise reach retained diagnostics through
        ``errors="replace"`` with its bytes intact.
        """
        if not isinstance(value, (bytes, bytearray)):
            self.counters.suppressed_fields += 1
            return GUARDED_TEXT_WITHHELD_BYTES
        raw = bytes(value)
        if not self._byte_literals or not raw:
            return raw
        spans = _claim_spans(raw, self._byte_literals)
        if not spans:
            return raw
        self.counters.matched_occurrences += len(spans)
        guarded = _rebuild(raw, spans, ENV_VALUE_REPLACEMENT_BYTES, b"")
        if self.matches_bytes(guarded):
            self.counters.suppressed_fields += 1
            return GUARDED_TEXT_WITHHELD_BYTES
        return guarded

    def guard_prefix(
        self, value: str, *, keep_tail: int | None = None
    ) -> tuple[str, str]:
        """Guard the resolved prefix; return ``(emitted, carry)``.

        ``keep_tail`` defaults to ``max_literal_chars - 1``. Every literal that
        begins inside the emitted prefix therefore ends inside ``value`` as
        well, so a value deliberately split across two ACP chunks is seen whole
        before either half is retained. A matched span that straddles the cut
        moves the cut to its end, so the token is emitted intact rather than
        halved.
        """
        if not isinstance(value, str):
            self.counters.suppressed_fields += 1
            return (GUARDED_VALUE_WITHHELD, "")
        if keep_tail is None:
            keep_tail = self._max_literal_chars - 1 if self._max_literal_chars else 0
        if keep_tail < 0:
            keep_tail = 0
        if not self._literals:
            return (value, "")
        cut = len(value) - keep_tail
        if cut <= 0:
            return ("", value)
        spans = _claim_spans(value, self._literals)
        moved = True
        while moved:
            moved = False
            for start, end in spans:
                if start < cut < end:
                    cut = end
                    moved = True
        head_spans = [(start, end) for start, end in spans if end <= cut]
        head = value[:cut]
        if not head_spans:
            return (head, value[cut:])
        self.counters.matched_occurrences += len(head_spans)
        emitted = _rebuild(head, head_spans, ENV_VALUE_REPLACEMENT, "")
        if self.matches(emitted):
            self.counters.suppressed_fields += 1
            emitted = GUARDED_TEXT_WITHHELD
        return (emitted, value[cut:])

    def safe_text(self, value: str) -> SafeText:
        """The only guard-produced free-form projection type."""
        return SafeText(self.guard_text(value), _SAFE_TEXT_MINT)

    # -- structured values -------------------------------------------------

    def guard_value(self, value: Any) -> Any:
        """Recursively guard dynamic keys and values of child-controlled data."""
        guarded = self._guard_value_inner(value)
        if guarded is _WITHHELD_RECORD:
            return {"withheld": True, "withheld_reason": GUARDED_RECORD_WITHHELD}
        return guarded

    def guard_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Guard one event record, preserving only a demonstrably clean family.

        A suppressed record keeps its ``type`` only when that family survives
        the guard byte-for-byte; a family that is itself a projected value
        degrades to ``unknown_update`` rather than leaking through the one
        field a reader trusts.
        """
        if not isinstance(event, Mapping):
            self.counters.suppressed_records += 1
            return {
                "type": "unknown_update",
                "withheld": True,
                "withheld_reason": GUARDED_RECORD_WITHHELD,
            }
        guarded = self._guard_value_inner(dict(event))
        if guarded is not _WITHHELD_RECORD:
            return guarded
        family = event.get("type")
        safe_family = "unknown_update"
        if isinstance(family, str) and self.guard_text(family) == family:
            safe_family = family
        return {
            "type": safe_family,
            "withheld": True,
            "withheld_reason": GUARDED_RECORD_WITHHELD,
        }

    def _guard_value_inner(self, value: Any) -> Any:
        if value is None or type(value) in (bool, int, float):
            return value
        if isinstance(value, str):
            return self.guard_text(value)
        if isinstance(value, (bytes, bytearray)):
            return self.guard_bytes(value)
        if isinstance(value, Mapping):
            return self._guard_mapping(value)
        if isinstance(value, (list, tuple)):
            return [self.guard_value(item) for item in value]
        # Anything else would have to be stringified to be projected, and a
        # hostile ``__str__``/``__repr__`` is exactly the shape that leaks.
        self.counters.suppressed_fields += 1
        return GUARDED_VALUE_WITHHELD

    def _guard_mapping(self, mapping: Mapping[Any, Any]) -> Any:
        guarded_keys: list[Any] = []
        items: list[tuple[Any, Any]] = []
        for key, sub in mapping.items():
            if isinstance(key, str):
                guarded_key: Any = self.guard_text(key)
            elif key is None or type(key) in (bool, int, float):
                guarded_key = key
            else:
                self.counters.suppressed_records += 1
                return _WITHHELD_RECORD
            # Two distinct keys collapsing onto one guarded key would silently
            # drop a field and imply which of them held a projected value.
            if any(guarded_key == existing for existing in guarded_keys):
                self.counters.suppressed_records += 1
                return _WITHHELD_RECORD
            guarded_keys.append(guarded_key)
            items.append((guarded_key, self.guard_value(sub)))
        return dict(items)

    # -- confidentiality of the carrier itself -----------------------------

    def __repr__(self) -> str:
        return "<RunTextGuard sensitive set withheld>"

    def __reduce__(self):
        raise TypeError(
            "RunTextGuard is ephemeral and must never be serialized or copied"
        )


# Every fixed marker this module can insert into a projection. An occurrence
# that overlaps one of these is, by construction, sitting where the guard
# itself edited the document.
_REPLACEMENT_MARKS: tuple[str, ...] = (
    ENV_VALUE_REPLACEMENT,
    GUARDED_TEXT_WITHHELD,
    GUARDED_VALUE_WITHHELD,
    GUARDED_RECORD_WITHHELD,
)


def _mark_spans(rendered: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for mark in _REPLACEMENT_MARKS:
        start = 0
        while True:
            index = rendered.find(mark, start)
            if index < 0:
                break
            spans.append((index, index + len(mark)))
            start = index + 1
    return spans


def serialized_projection_is_safe(
    guard: "RunTextGuard | None", rendered: str
) -> bool:
    """Postcondition over the **final serialized** form of a guarded document.

    Field-wise guarding never sees the bytes a serializer inserts *between*
    fields — quotes, separators, indentation — nor an identifier assigned
    after guarding, such as a sequence number. A projected value can therefore
    be recomposed across a replacement token and that punctuation even though
    no single field ever contained it, which is unreachable for every matcher
    that runs before composition exists.

    It is deliberately **not** a blanket scan of the rendered bytes. Two
    disjoint conditions make an occurrence recomposition rather than
    coincidence:

    * the literal contains a raw ``"`` or newline — in rendered JSON those
      characters only ever appear as structure, because a quote or newline
      inside a value is escaped, so such an occurrence necessarily spans more
      than one serialized atom;
    * the occurrence overlaps a marker this module inserted — it exists only
      because the guard edited that spot.

    Everything else is a value whose bytes coincide with an independently
    derived public fact — a schema version, an exit code, a sequence number —
    and erasing evidence for that would be the over-erasure this stage is
    explicitly warned against. A one-character value equal to a sequence
    number does not suppress a record.
    """
    if guard is None or not rendered:
        return True
    marks = _mark_spans(rendered)
    for literal in guard._literals:
        spans_structure = '"' in literal or "\n" in literal
        width = len(literal)
        start = 0
        while True:
            index = rendered.find(literal, start)
            if index < 0:
                break
            if spans_structure:
                return False
            if not _span_is_free(marks, index, index + width):
                return False
            start = index + 1
    return True

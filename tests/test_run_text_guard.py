"""L1: the ephemeral per-Run literal guard (V4 §6.3, Stage 2 WP2.1).

Every environment value is sensitive regardless of key name, shape, or
length. The guard is the single matcher the sink boundary is built on, so it
is proven here directly rather than through a sink: exact string and
``os.fsencode`` byte matchers, longest-first overlap resolution, duplicate
removal by direct equality, recursive dynamic key/value guarding, whole-record
suppression on a guarded-key collision, a postcondition rescan that withholds
rather than emits, coarse counters only, and — structurally — no hash, digest,
serializer, equality token, or diagnostic enumeration of a sensitive value,
not even transiently.
"""

from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path

import pytest

from agent_run_supervisor.redaction import (
    EMPTY_SAFE_TEXT,
    ENV_VALUE_REPLACEMENT,
    ENV_VALUE_REPLACEMENT_BYTES,
    GUARDED_RECORD_WITHHELD,
    GUARDED_TEXT_WITHHELD,
    GUARDED_TEXT_WITHHELD_BYTES,
    GUARDED_VALUE_WITHHELD,
    RunTextGuard,
    SafeText,
    redact_argv,
)

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "agent_run_supervisor"


class _HashTrap(str):
    """A string that screams if anything hashes it.

    Set/dict membership, ``hash()``, and every other hash-consuming operation
    route through ``__hash__``; a guard that keeps its sensitive set in a set,
    a dict key, a regex cache keyed by the value, or a digest cannot avoid it.
    Equality still works, so direct-equality duplicate removal is unaffected.
    """

    def __hash__(self) -> int:  # pragma: no cover - the assertion is the point
        raise AssertionError("a sensitive value was hashed")


def _guard(*values: str) -> RunTextGuard:
    return RunTextGuard.from_environment(
        {f"NAME_{index}": value for index, value in enumerate(values)}
    )


# -- exact matchers --------------------------------------------------------


def test_exact_string_literal_is_replaced_by_the_fixed_token() -> None:
    guard = _guard("s3cret-value")

    assert guard.guard_text("prefix s3cret-value suffix") == (
        f"prefix {ENV_VALUE_REPLACEMENT} suffix"
    )


def test_every_occurrence_of_one_literal_is_replaced() -> None:
    guard = _guard("abc")

    guarded = guard.guard_text("abc / abc / abc")

    assert "abc" not in guarded
    assert guarded.count(ENV_VALUE_REPLACEMENT) == 3


def test_exact_byte_literal_is_replaced_through_fsencode() -> None:
    value = "vàlue-ø"
    guard = _guard(value)

    raw = b"stderr: " + os.fsencode(value) + b" tail"
    guarded = guard.guard_bytes(raw)

    assert os.fsencode(value) not in guarded
    assert ENV_VALUE_REPLACEMENT_BYTES in guarded


def test_byte_matcher_survives_undecodable_neighbours() -> None:
    guard = _guard("token-bytes")

    raw = b"\xff\xfe" + b"token-bytes" + b"\x80"
    guarded = guard.guard_bytes(raw)

    assert b"token-bytes" not in guarded
    assert guarded.startswith(b"\xff\xfe")


def test_empty_values_contribute_no_bytes() -> None:
    guard = RunTextGuard.from_environment({"EMPTY": "", "REAL": "abc"})

    # An empty literal would otherwise match at every position.
    assert guard.guard_text("xyz") == "xyz"
    assert guard.guard_text("abc") == ENV_VALUE_REPLACEMENT


def test_a_run_without_environment_values_is_a_pass_through() -> None:
    guard = RunTextGuard.from_environment({})

    assert guard.guard_text("anything at all") == "anything at all"
    assert guard.guard_bytes(b"anything at all") == b"anything at all"
    assert guard.max_literal_chars == 0


# -- overlap and duplicates ------------------------------------------------


def test_overlapping_values_are_matched_longest_first() -> None:
    # "abcdef" and "cd" both occur; the longer literal must claim the span so
    # a short value can never fragment a long one into surviving pieces.
    guard = _guard("cd", "abcdef")

    guarded = guard.guard_text("xx abcdef yy")

    assert guarded == f"xx {ENV_VALUE_REPLACEMENT} yy"
    assert guarded.count(ENV_VALUE_REPLACEMENT) == 1


def test_overlapping_claims_leave_no_literal_behind() -> None:
    guard = _guard("bcd", "abc")

    guarded = guard.guard_text("abcd")

    assert "abc" not in guarded
    assert "bcd" not in guarded


def test_duplicate_values_are_removed_by_direct_equality() -> None:
    guard = RunTextGuard.from_environment(
        {"A": "same-value", "B": "same-value", "C": "same-value"}
    )

    guarded = guard.guard_text("same-value")

    assert guarded == ENV_VALUE_REPLACEMENT
    # One occurrence, matched once — not once per duplicated name.
    assert guard.report()["matched_occurrences"] == 1


# -- recursion, dynamic keys, record suppression ---------------------------


def test_recursive_dynamic_keys_and_values_are_guarded() -> None:
    guard = _guard("leak")

    guarded = guard.guard_value(
        {
            "leak-key": {"inner": ["a leak here", {"deep": "leak"}]},
            "plain": "safe",
        }
    )

    assert f"{ENV_VALUE_REPLACEMENT}-key" in guarded
    assert guarded[f"{ENV_VALUE_REPLACEMENT}-key"]["inner"][0] == (
        f"a {ENV_VALUE_REPLACEMENT} here"
    )
    assert guarded[f"{ENV_VALUE_REPLACEMENT}-key"]["inner"][1]["deep"] == (
        ENV_VALUE_REPLACEMENT
    )
    assert guarded["plain"] == "safe"


def test_guarded_key_collision_suppresses_the_whole_record() -> None:
    guard = _guard("alpha", "beta")

    guarded = guard.guard_value({"alpha": 1, "beta": 2})

    # Both keys guard to the same token: keeping either one would silently
    # drop a field and imply which value it held.
    assert guarded == {
        "withheld": True,
        "withheld_reason": GUARDED_RECORD_WITHHELD,
    }
    assert guard.report()["suppressed_records"] == 1


def test_guard_event_keeps_only_a_clean_family_when_suppressed() -> None:
    guard = _guard("alpha", "beta")

    guarded = guard.guard_event({"type": "tool_started", "alpha": 1, "beta": 2})

    assert guarded["type"] == "tool_started"
    assert guarded["withheld"] is True
    assert guarded["withheld_reason"] == GUARDED_RECORD_WITHHELD
    assert "alpha" not in guarded and "beta" not in guarded


def test_guard_event_drops_a_family_that_is_itself_sensitive() -> None:
    guard = _guard("tool_started", "alpha", "beta")

    guarded = guard.guard_event({"type": "tool_started", "alpha": 1, "beta": 2})

    assert guarded["type"] == "unknown_update"
    assert guarded["withheld"] is True


def test_unsupported_value_types_are_withheld_not_stringified() -> None:
    class Hostile:
        def __str__(self) -> str:  # pragma: no cover - must never be called
            return "leak"

        __repr__ = __str__

    guard = _guard("leak")

    guarded = guard.guard_value({"k": Hostile()})

    assert guarded == {"k": GUARDED_VALUE_WITHHELD}
    assert guard.report()["suppressed_fields"] == 1


def test_numbers_and_none_pass_through_unchanged() -> None:
    guard = _guard("leak")

    assert guard.guard_value({"a": 1, "b": 1.5, "c": True, "d": None}) == {
        "a": 1,
        "b": 1.5,
        "c": True,
        "d": None,
    }


# -- postcondition rescan --------------------------------------------------


def test_postcondition_rescan_withholds_when_the_token_reintroduces_a_literal() -> None:
    # A value that is itself a substring of the replacement token makes safe
    # replacement impossible: confidentiality wins over evidence completeness.
    guard = _guard("ENV_VALUE")

    guarded = guard.guard_text("observed ENV_VALUE here")

    assert guarded == GUARDED_TEXT_WITHHELD
    assert guard.report()["suppressed_fields"] == 1


def test_postcondition_rescan_withholds_bytes_too() -> None:
    guard = _guard("ENV_VALUE")

    guarded = guard.guard_bytes(b"observed ENV_VALUE here")

    assert guarded == GUARDED_TEXT_WITHHELD_BYTES


def test_a_clean_replacement_is_not_withheld() -> None:
    guard = _guard("ordinary")

    assert guard.guard_text("an ordinary line") == f"an {ENV_VALUE_REPLACEMENT} line"
    assert guard.report()["suppressed_fields"] == 0


# -- streaming carry -------------------------------------------------------


def test_guard_prefix_holds_back_the_rolling_carry() -> None:
    guard = _guard("SPLITME")

    emitted, carry = guard.guard_prefix("chunk-one SPLIT")

    # ``max_literal_chars - 1`` characters stay unemitted so a literal that
    # straddles two chunks is still seen whole.
    assert "SPLIT" not in emitted
    assert carry.endswith("SPLIT")

    emitted2, carry2 = guard.guard_prefix(carry + "ME tail")
    assert "SPLITME" not in emitted2
    assert ENV_VALUE_REPLACEMENT in emitted2
    assert "SPLITME" not in carry2


def test_guard_prefix_emits_a_matched_literal_that_straddles_the_cut() -> None:
    guard = _guard("abcdef")

    emitted, carry = guard.guard_prefix("xx abcdef")

    assert ENV_VALUE_REPLACEMENT in emitted
    assert "abcdef" not in emitted + carry


def test_guard_prefix_without_literals_emits_everything() -> None:
    guard = RunTextGuard.from_environment({})

    emitted, carry = guard.guard_prefix("all of it")

    assert emitted == "all of it"
    assert carry == ""


# -- counters --------------------------------------------------------------


def test_report_records_only_coarse_counters() -> None:
    guard = _guard("value-one")
    guard.guard_text("value-one and value-one")
    guard.guard_value({"x": object()})

    report = guard.report()

    assert set(report) == {
        "matched_occurrences",
        "suppressed_fields",
        "suppressed_records",
    }
    assert all(isinstance(count, int) for count in report.values())
    assert report["matched_occurrences"] == 2


def test_report_never_records_a_length_by_value() -> None:
    long_value = "L" * 97
    guard = _guard(long_value)
    guard.guard_text(f"here: {long_value}")

    report = guard.report()

    assert 97 not in report.values()
    assert len(ENV_VALUE_REPLACEMENT) not in report.values()
    assert guard.report() == report


# -- structural confidentiality of the sensitive set -----------------------


def test_no_hash_of_a_sensitive_value_is_computed_even_transiently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbidden(*args, **kwargs):  # pragma: no cover - assertion is the point
        raise AssertionError("a digest was computed over guard input")

    for name in ("new", "md5", "sha1", "sha256", "sha512", "blake2b", "blake2s"):
        monkeypatch.setattr(hashlib, name, _forbidden, raising=False)

    trapped = _HashTrap("trap-value")
    guard = RunTextGuard.from_environment({"A": trapped, "B": _HashTrap("trap-value")})

    guarded = guard.guard_value(
        {"trap-value": ["trap-value", {"n": "x trap-value y"}]}
    )
    guard.guard_bytes(b"raw trap-value raw")
    guard.guard_prefix("stream trap-value stream")
    guard.matches("trap-value")

    assert "trap-value" not in repr(guarded)


def test_guard_module_imports_no_digest_library() -> None:
    tree = ast.parse((SRC_ROOT / "redaction.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert not imported & {"hashlib", "hmac", "secrets", "pickle", "marshal"}


def test_the_guard_exposes_no_serializer_or_enumeration() -> None:
    guard = _guard("hidden-value")

    public = {name for name in dir(guard) if not name.startswith("_")}
    # The complete non-private surface, pinned exactly: a serializer, an
    # enumerator, or a digest helper cannot be added without failing here.
    # ``from_environment`` is the plan-named factory (exact invariant 1) and is
    # the one public member that touches values — it takes the resolved
    # environment *in* and returns a guard, never a value.
    assert public == {
        "clear",
        "counters",
        "from_environment",
        "guard_bytes",
        "guard_event",
        "guard_prefix",
        "guard_text",
        "guard_value",
        "matches",
        "matches_bytes",
        "max_literal_bytes",
        "max_literal_chars",
        "report",
        "safe_text",
    }
    for forbidden in ("to_dict", "to_json", "as_dict", "literals", "values"):
        assert not hasattr(guard, forbidden)

    # The factory hands back a guard, not the set it was built from, and the
    # only value-shaped things any public member returns are guarded outputs.
    built = RunTextGuard.from_environment({"NAME": "hidden-value"})
    assert isinstance(built, RunTextGuard)
    assert "hidden-value" not in repr(built)
    assert guard.matches("hidden-value") is True
    assert set(guard.report()) == {
        "matched_occurrences",
        "suppressed_fields",
        "suppressed_records",
    }
    assert isinstance(guard.max_literal_chars, int)
    assert isinstance(guard.max_literal_bytes, int)


def test_the_guard_refuses_to_be_serialized() -> None:
    guard = _guard("hidden-value")

    with pytest.raises(TypeError):
        guard.__reduce__()


def test_the_guard_defines_no_value_derived_equality_or_hash() -> None:
    assert RunTextGuard.__eq__ is object.__eq__
    assert RunTextGuard.__hash__ is object.__hash__


def test_repr_never_shows_the_sensitive_set() -> None:
    guard = _guard("hidden-value")

    assert "hidden-value" not in repr(guard)
    assert "hidden-value" not in str(vars(type(guard)))


def test_clear_drops_the_sensitive_set() -> None:
    guard = _guard("hidden-value")
    assert guard.matches("hidden-value")

    guard.clear()

    assert not guard.matches("hidden-value")
    assert guard.guard_text("hidden-value") == "hidden-value"


def test_matches_bytes_reports_the_byte_matcher() -> None:
    guard = _guard("vàlue")

    assert guard.matches_bytes(b"x" + os.fsencode("vàlue"))
    assert not guard.matches_bytes(b"nothing here")


def test_the_replacement_token_is_a_fixed_source_literal() -> None:
    guard = _guard("first-value")
    other = _guard("second-value")

    assert guard.guard_text("first-value") == other.guard_text("second-value")
    assert ENV_VALUE_REPLACEMENT_BYTES == ENV_VALUE_REPLACEMENT.encode("utf-8")


# -- safe projection type --------------------------------------------------


def test_safe_text_is_produced_by_the_guard_and_carries_guarded_text() -> None:
    guard = _guard("secret")

    projection = guard.safe_text("a secret line")

    assert type(projection) is SafeText
    assert projection.text == f"a {ENV_VALUE_REPLACEMENT} line"
    assert str(projection) == projection.text
    assert EMPTY_SAFE_TEXT.text == ""


def test_safe_text_cannot_be_minted_by_an_ordinary_constructor_call() -> None:
    """B2: a type barrier a caller can simply construct is not a barrier.

    ``SafeText`` is the runtime proof that free-form text crossed the guard.
    While an ordinary ``SafeText(raw_child_text)`` call produces the exact
    accepted type, every seam that checks the type is decoration: the shortest
    path around the guard is one constructor call.

    This blocks ordinary supported construction. It is deliberately **not** a
    claim to survive arbitrary in-process reflection such as
    ``object.__new__`` — that is not a boundary Python offers, and pretending
    otherwise would be the kind of over-claim the authority forbids.
    """
    with pytest.raises(TypeError):
        SafeText("raw child text")


def test_only_the_guard_and_the_source_owned_empty_value_mint_safe_text() -> None:
    guard = _guard("secret")

    minted = guard.safe_text("a secret line")

    assert type(minted) is SafeText
    assert minted.text == f"a {ENV_VALUE_REPLACEMENT} line"
    assert type(EMPTY_SAFE_TEXT) is SafeText
    assert EMPTY_SAFE_TEXT.text == ""


def test_safe_text_is_constructed_only_inside_the_redaction_module() -> None:
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path.name == "redaction.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "SafeText"
            ):
                offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == []


# -- reviewer note 6: the argv note must be value-blind --------------------


def test_redact_argv_note_is_value_blind() -> None:
    secret = "p@ssw0rd-not-a-real-one"

    redacted, report = redact_argv(["agent", "--password", secret])

    assert secret not in redacted
    assert all(secret not in match.note for match in report.matches)
    assert any(match.pattern_name == "argv_sensitive_flag" for match in report.matches)

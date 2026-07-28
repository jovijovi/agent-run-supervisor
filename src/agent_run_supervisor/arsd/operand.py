"""One admission rule for every caller-supplied path-or-text operand of ``arsd``.

Judge an operand's type by identity **before** any of its own code runs, read it
exactly once, admit the *result* of that read by identity as a plain ``str``,
and hand every later reader that frozen text.

Coercion is not inspection. ``str(x)``, ``Path(x)``, ``for ch in x``,
``x.startswith(...)`` and ``x.split(...)`` all ask the operand a question by
running its code. Two consequences follow mechanically: caller code runs before
the operand has been admitted, and any value read more than once may answer
differently each time — including the result of a read that was itself never
admitted.

Type identity is the only form of the question that cannot be intercepted. A
subclass check admits subclasses and, when the concrete type does not match, it
also consults a ``__class__`` attribute the operand supplies, so an ordinary
object can simply claim to be a ``str``. Membership in a tuple of types compares
with ``==``, which a hostile metaclass answers. The path protocol is caller code
by definition. ``type(v) is T`` can be neither overridden nor intercepted.

This is a leaf module on purpose. It imports ``os`` and ``pathlib`` and nothing
from this package, so the daemon entrypoint and the systemd renderer can both
depend on it without an import cycle and without a second copy of the rule —
and, unlike a helper living in ``__main__``, it is one module object however it
is reached.

Nothing here touches the filesystem, and nothing here reads a process-wide
setting. ARS never creates, writes, repairs, promotes, or migrates the operator
Runtime Binding root, and the per-Run ``BindingReader`` remains its first and
only reader (PRD R13, C7/C8).
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "EXACT_PATH_TYPE",
    "OperandError",
    "admit_exact_text",
    "capture_binding_root",
]


class OperandError(ValueError):
    """Fail-closed, sanitized refusal of an inadmissible boundary operand."""


# Derived, not named: ``Path`` builds the platform's concrete path class, and
# *that* class — not a subclass of it, and not some other path-like object — is
# what a declared ``Path | str`` union means here. Pure constructor work.
EXACT_PATH_TYPE = type(Path(os.sep))

# The admitted sets, one per calling surface's own declared signature. Tuples of
# type objects, compared one at a time with ``is`` — never ``in``, never ``==``.
_EXACT_STR = (str,)
_EXACT_STR_OR_PATH = (str, EXACT_PATH_TYPE)


def _inadmissible_type(label: str, allow_path: bool) -> str:
    """Fixed refusal text: it names the contract, never the operand."""
    if allow_path:
        return f"{label} must be a plain str or Path"
    return f"{label} must be a plain str"


def admit_exact_text(value: object, *, label: str, allow_path: bool = False) -> str:
    """Admit ``value`` by type identity and return its frozen text.

    ``allow_path`` selects the admitted set from the calling surface's declared
    signature: text only for the unit renderer, text or a concrete path for the
    daemon entrypoint. One rule, two admitted sets — never two rules.

    Applies **no** shape rules. Non-empty, control-free and absolute belong to
    :func:`capture_binding_root` alone, because that is where they live today.

    The returned text is safe to share by identity: a ``str`` is immutable, so no
    later reader can be shown a different answer, and no reference to the
    caller's object survives in the result.
    """
    admitted = _EXACT_STR_OR_PATH if allow_path else _EXACT_STR
    for candidate in admitted:
        if type(value) is candidate:
            break
    else:
        raise OperandError(_inadmissible_type(label, allow_path))
    # Exactly one read. An exact ``str`` already *is* the text; reading it again
    # would be a second read that could only weaken the guarantee.
    text = value if type(value) is str else str(value)
    # Admit the result of that read by identity too. ``PyObject_Str`` validates
    # what the read returned with a check that admits subclasses, and a concrete
    # path keeps its text in an assignable slot — so even an operand of the exact
    # admitted type can hand back a ``str`` subclass whose every method is caller
    # code. Refuse it: coercing it once more would accept text the operand chose,
    # after having run it.
    if type(text) is not str:
        raise OperandError(f"{label} did not read back as a plain str")
    return text


def capture_binding_root(value: object) -> str:
    """Admit, shape-check and freeze the operator Runtime Binding root (PRD R13).

    Shape only, and only *after* admission: non-blank, control-free, and
    explicitly absolute. There is no fallback, no PATH lookup, and no
    service-UID-owned default — an unconfigured daemon stays fail-closed rather
    than guessing a root.

    Both doors call this one: ``parse_binding_root`` (argv) and ``serve_daemon``
    (programmatic). ``main()`` is not the only entry — every embedder calls the
    coroutine directly — so both must apply the same contract, or the weaker one
    becomes the real one.

    Every refusal text is fixed and quotes nothing: reporting the operand would
    hand an operator back whatever text the operand's own code chose.
    """
    text = admit_exact_text(value, label="binding root", allow_path=True)
    if not text.strip():
        raise OperandError("binding root must be a non-empty absolute path")
    for ch in text:
        if ord(ch) < 32 or ord(ch) == 127:
            raise OperandError("binding root contains control characters")
    if not text.startswith("/"):
        raise OperandError("binding root must be an absolute path")
    return text

"""Mandatory handler-level log containment for the per-Run literal guard.

Why a **handler** filter and not a logger filter: a logger applies its filters
only to records logged *through it*, while a record propagated from a child
logger reaches the ancestor's handlers directly. Every record that is actually
emitted passes a handler, so the handler is the only seam that sees all of
them — including the ACP SDK's module-level ``logging.exception`` calls and
any dependency ARS never named.

The existing root-**logger** redactor in ``native_acp/__init__`` stays as
defense in depth beneath this filter; it strips exception detail from the SDK's
root-logged records even outside a Run.

Three rules, in order:

1. **Outside every Run context the record is untouched.** ARS's own
   diagnostics keep their exception detail when no environment value can be in
   flight.
2. **Inside a Run context** the record's ``msg``/``args`` are rendered once,
   guarded as one complete string, and the raw ``args``/``exc_info``/
   ``stack_info`` are cleared — a formatter must never re-render child data
   after the guard ran. Every dependency/SDK-originated record is replaced
   wholesale, because its message layout is not ARS's to reason about.
3. **A Run-tagged record with no guard in its own context is suppressed**
   categorically as ``UNSANITIZED_RUN_LOG_SUPPRESSED``.

Reviewer note 3: ``contextvars`` do not cross a raw thread boundary, so an
off-loop record carries no Run context at all. A process-wide registry of live
guards closes that structurally — while any Run is live, an untagged
off-context record is guarded against *every* live guard rather than trusted.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from dataclasses import dataclass
from typing import Iterator, Sequence

from ..redaction import RunTextGuard

# Categorical markers. Each is a fixed source literal carrying no input data.
UNSANITIZED_RUN_LOG_SUPPRESSED = "UNSANITIZED_RUN_LOG_SUPPRESSED"
DEPENDENCY_RECORD_REPLACED = "DEPENDENCY_LOG_RECORD_REPLACED"
UNFORMATTABLE_RECORD_REPLACED = "UNFORMATTABLE_LOG_RECORD_REPLACED"
GUARD_FAILURE_RECORD_REPLACED = "LOG_GUARD_FAILURE_RECORD_REPLACED"

_ARS_LOGGER_ROOT = "agent_run_supervisor"

_CURRENT_GUARD: contextvars.ContextVar["RunTextGuard | None"] = contextvars.ContextVar(
    "ars_run_text_guard", default=None
)
_CURRENT_RUN_ID: contextvars.ContextVar["str | None"] = contextvars.ContextVar(
    "ars_run_id", default=None
)

_ACTIVE_LOCK = threading.Lock()
_ACTIVE_GUARDS: list[RunTextGuard] = []


@dataclass(frozen=True)
class RunGuardBinding:
    """Handle for one Run's guard binding; reset restores the prior context."""

    guard: RunTextGuard
    guard_token: object
    run_id_token: object


def current_run_guard() -> RunTextGuard | None:
    return _CURRENT_GUARD.get()


def current_run_id() -> str | None:
    return _CURRENT_RUN_ID.get()


def active_guards() -> tuple[RunTextGuard, ...]:
    with _ACTIVE_LOCK:
        return tuple(_ACTIVE_GUARDS)


def bind_run_guard(guard: RunTextGuard, *, run_id: str) -> RunGuardBinding:
    """Install the filter, then bind the guard to this context and the process.

    Installation happens first and unconditionally: a guard that is live while
    an unfiltered handler exists is exactly the hole this module closes, so the
    two facts are bound together rather than left to call-site discipline.
    """
    install_safe_logging()
    with _ACTIVE_LOCK:
        _ACTIVE_GUARDS.append(guard)
    return RunGuardBinding(
        guard=guard,
        guard_token=_CURRENT_GUARD.set(guard),
        run_id_token=_CURRENT_RUN_ID.set(run_id),
    )


def unbind_run_guard(binding: RunGuardBinding | None) -> None:
    """Drop the binding. Safe to call twice; never raises into finalization."""
    if binding is None:
        return
    with _ACTIVE_LOCK:
        for index in range(len(_ACTIVE_GUARDS) - 1, -1, -1):
            if _ACTIVE_GUARDS[index] is binding.guard:
                del _ACTIVE_GUARDS[index]
                break
    for variable, token in (
        (_CURRENT_GUARD, binding.guard_token),
        (_CURRENT_RUN_ID, binding.run_id_token),
    ):
        try:
            variable.reset(token)  # type: ignore[arg-type]
        except (ValueError, RuntimeError):
            variable.set(None)  # type: ignore[arg-type]


class RunTextGuardFilter(logging.Filter):
    """Handler-level containment. Always returns True; never drops a record.

    Suppression here means *replacing the payload with a categorical marker*,
    not silence: an operator must still see that a Run-scoped record existed
    and why it could not be projected.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            self._contain(record)
        except Exception:
            # A filter that raises breaks logging for everyone. Fail closed on
            # the record instead: the payload is exactly what we cannot trust.
            record.msg = GUARD_FAILURE_RECORD_REPLACED
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True

    def _contain(self, record: logging.LogRecord) -> None:
        guard = _CURRENT_GUARD.get()
        tagged = _CURRENT_RUN_ID.get() is not None or (
            getattr(record, "run_id", None) is not None
        )
        active = active_guards()
        if guard is None and not tagged and not active:
            return

        record.stack_info = None
        record.exc_info = None
        record.exc_text = None

        if guard is None:
            if tagged:
                # Run-tagged but unguarded: which Run's values could be in this
                # text is unknowable, so the whole payload goes.
                _replace(record, UNSANITIZED_RUN_LOG_SUPPRESSED)
                return
            guards: Sequence[RunTextGuard] = active
        else:
            guards = (guard,)

        if not _is_ars_record(record):
            _replace(
                record,
                f"{DEPENDENCY_RECORD_REPLACED} logger={_safe_logger_name(record, guards)}",
            )
            return

        text = _rendered(record)
        for one in guards:
            text = one.guard_text(text)
        _replace(record, text)


_FILTER = RunTextGuardFilter()


def _replace(record: logging.LogRecord, text: str) -> None:
    record.msg = text
    record.args = ()


def _rendered(record: logging.LogRecord) -> str:
    """The complete preformatted ``msg % args``, or a categorical marker.

    Guarding ``msg`` and ``args`` separately would miss a value that only
    exists once they are interpolated.
    """
    try:
        rendered = record.getMessage()
    except Exception:
        return UNFORMATTABLE_RECORD_REPLACED
    return rendered if isinstance(rendered, str) else UNFORMATTABLE_RECORD_REPLACED


def _is_ars_record(record: logging.LogRecord) -> bool:
    name = record.name
    if not isinstance(name, str):
        return False
    return name == _ARS_LOGGER_ROOT or name.startswith(f"{_ARS_LOGGER_ROOT}.")


def _safe_logger_name(
    record: logging.LogRecord, guards: Sequence[RunTextGuard]
) -> str:
    name = record.name if isinstance(record.name, str) else "unknown"
    root = name.split(".", 1)[0]
    for one in guards:
        root = one.guard_text(root)
    return root


# -- installation ----------------------------------------------------------


def install_safe_logging() -> None:
    """Attach the filter to every root handler and to ``logging.lastResort``.

    Idempotent by filter identity, and re-appliable: a handler installed after
    an earlier call (a test capture handler, a late ``basicConfig``) is covered
    by the next call, which every guard binding performs.
    """
    for handler in _root_handlers():
        if not any(item is _FILTER for item in handler.filters):
            handler.addFilter(_FILTER)


def safe_logging_installed() -> bool:
    handlers = _root_handlers()
    if not handlers:
        return False
    return all(
        any(item is _FILTER for item in handler.filters) for handler in handlers
    )


def _root_handlers() -> list[logging.Handler]:
    handlers = list(logging.getLogger().handlers)
    last_resort = getattr(logging, "lastResort", None)
    if isinstance(last_resort, logging.Handler):
        handlers.append(last_resort)
    return handlers


def iter_guard_bindings() -> Iterator[RunTextGuard]:
    """Live guards, for tests and for the off-loop containment proof."""
    yield from active_guards()

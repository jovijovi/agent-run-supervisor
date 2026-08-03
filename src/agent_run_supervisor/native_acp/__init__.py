"""Native ACP core (vNext Stage 1) — additive to the released acpx baseline.

No eager SDK import: the stdlib modules of this package import cleanly
without the ``native`` extra; only SDK-needing modules (driver/client) call
:func:`require_sdk` and surface :class:`NativeSdkUnavailableError` on use.
Nothing in this package may construct acpx invocations or read legacy
stores — structurally pinned by the native_acp test suites.
"""

from __future__ import annotations

import importlib
import logging


class NativeSdkUnavailableError(RuntimeError):
    """The pinned ACP SDK is not installed in this environment."""


class _RootExceptionDetailRedactor(logging.Filter):
    """Drop exception detail from records emitted *on* the root logger.

    SDK 0.12.0 logs a handler failure with ``logging.exception(...,
    exc_info=exc)`` in ``_run_request`` and ``_run_notification``, unchanged
    from 0.11.1 (0.11.0 answered the request and suppressed the notification,
    emitting nothing).
    Those are module-level calls, so the records are emitted on the root logger
    — the one ``arsd`` configures — and their ``exc_info`` carries two things
    ARS redacts everywhere else: the wire values a rejected ``session/update``
    frame contained, rendered by ``pydantic`` into its ``ValidationError``, and
    whatever an injected ARS handler raised with, such as an ``OSError`` naming
    an absolute workspace path.

    The rejected-frame case is raised inside the SDK's own params validation,
    before any :class:`~.client.NativeAcpClient` callback runs, so it cannot be
    contained at the callback boundary — the record itself is the only seam.

    Scope is exactly right by construction: a logger applies its filters only to
    records logged *through it*, while records propagated from a child logger
    reach its handlers directly. ARS always logs through named loggers, so this
    sees the SDK's root-logged records and never ARS's own diagnostics.

    Redaction, not silence: the record survives with its message (the failing
    ACP method) and the exception's class name, so an operator still sees that a
    handler failed and how. The detail is replaced rather than re-rendered, so
    no ``msg``/``args`` interpolation happens here and a malformed record still
    fails where it would have without this filter.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # A rendered stack is the same class of disclosure as a traceback, so
        # it goes whether or not this record carries an exception.
        record.stack_info = None
        if record.exc_info is None and record.exc_text is None:
            return True
        exc_type = record.exc_info[0] if record.exc_info else None
        name = getattr(exc_type, "__name__", "exception")
        record.exc_info = None
        record.exc_text = f"[exception detail redacted: {name}]"
        return True


def _contain_sdk_root_logging() -> None:
    """Install :class:`_RootExceptionDetailRedactor` once per process."""
    root = logging.getLogger()
    if any(isinstance(f, _RootExceptionDetailRedactor) for f in root.filters):
        return
    root.addFilter(_RootExceptionDetailRedactor())


def require_sdk():
    """Import and return the pinned official ACP SDK module.

    Raises :class:`NativeSdkUnavailableError` when the ``native`` extra is not
    installed, so a base install fails on SDK *use*, never on package import.

    Taking the SDK on is also what installs its logging containment: every path
    that can reach the SDK's handler-exception logging builds a client first, so
    binding the two together leaves no uncontained entry point.
    """
    try:
        sdk = importlib.import_module("acp")
    except ImportError as exc:
        raise NativeSdkUnavailableError(
            "agent-client-protocol is not installed; "
            "install agent-run-supervisor with the 'native' extra"
        ) from exc
    _contain_sdk_root_logging()
    return sdk

"""Native ACP core (vNext Stage 1) — additive to the released acpx baseline.

No eager SDK import: the stdlib modules of this package import cleanly
without the ``native`` extra; only SDK-needing modules (driver/client) call
:func:`require_sdk` and surface :class:`NativeSdkUnavailableError` on use.
Nothing in this package may construct acpx invocations or read legacy
stores — structurally pinned by the native_acp test suites.
"""

from __future__ import annotations

import importlib


class NativeSdkUnavailableError(RuntimeError):
    """The pinned ACP SDK is not installed in this environment."""


def require_sdk():
    """Import and return the pinned official ACP SDK module.

    Raises :class:`NativeSdkUnavailableError` when the ``native`` extra is not
    installed, so a base install fails on SDK *use*, never on package import.
    """
    try:
        return importlib.import_module("acp")
    except ImportError as exc:
        raise NativeSdkUnavailableError(
            "agent-client-protocol is not installed; "
            "install agent-run-supervisor with the 'native' extra"
        ) from exc

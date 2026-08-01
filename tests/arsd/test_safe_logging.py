"""WP2.4: the mandatory handler-level log containment.

Logging is the sink with the fewest guarantees and the most authors: ARS's own
diagnostics, the ACP SDK's module-level ``logging.exception`` calls, and any
dependency that happens to be loaded. The filter therefore has to be positioned
where *every* emitted record passes — a handler — and its rules have to hold
for records ARS never wrote and for records that originate off the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

import pytest

from agent_run_supervisor.arsd import safe_logging, server
from agent_run_supervisor.redaction import ENV_VALUE_REPLACEMENT, RunTextGuard

SECRET = "log-sink-sentinel-value-9d41"


def _guard() -> RunTextGuard:
    return RunTextGuard.from_environment({"SECRET_NAME": SECRET})


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self) -> str:
        formatter = logging.Formatter("%(name)s|%(message)s")
        return "\n".join(formatter.format(record) for record in self.records)


@pytest.fixture()
def root_capture():
    """A real root handler, then the production installation call."""
    root = logging.getLogger()
    handler = _Capture()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    safe_logging.install_safe_logging()
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


@pytest.fixture()
def bound_guard():
    guard = _guard()
    binding = safe_logging.bind_run_guard(guard, run_id="run-log-1")
    try:
        yield guard
    finally:
        safe_logging.unbind_run_guard(binding)


def _ars_logger() -> logging.Logger:
    return logging.getLogger("agent_run_supervisor.arsd.testing.safe_logging")


def _dependency_logger() -> logging.Logger:
    return logging.getLogger("acp.connection")


# -- rule 1: outside a Run context nothing changes --------------------------


def test_outside_a_run_context_records_are_untouched(root_capture) -> None:
    assert safe_logging.current_run_guard() is None
    assert safe_logging.active_guards() == ()

    try:
        raise RuntimeError(SECRET)
    except RuntimeError:
        _ars_logger().exception("ars diagnostic %s", SECRET)

    record = root_capture.records[-1]
    assert record.exc_info is not None
    assert SECRET in root_capture.text()


# -- rule 2: inside a Run context, msg + args guarded as one string ---------


def test_ars_record_in_run_context_guards_the_interpolated_message(
    root_capture, bound_guard
) -> None:
    # Guarding ``msg`` and ``args`` separately would miss a value that only
    # exists once they are interpolated, so the rendered string is the unit.
    _ars_logger().error("resolved %s for %s", SECRET, "a run")

    assert SECRET not in root_capture.text()
    assert ENV_VALUE_REPLACEMENT in root_capture.text()
    assert "for a run" in root_capture.text()


def test_raw_args_and_exception_detail_are_cleared_in_run_context(
    root_capture, bound_guard
) -> None:
    # A formatter runs *after* every filter. Leaving the raw args in place
    # would let it re-render the value the guard just removed.
    try:
        raise RuntimeError(SECRET)
    except RuntimeError:
        _ars_logger().exception("ars failure %s", SECRET, stack_info=True)

    record = root_capture.records[-1]
    assert record.args == ()
    assert record.exc_info is None
    assert record.exc_text is None
    assert record.stack_info is None
    assert SECRET not in root_capture.text()


def test_an_unformattable_record_is_replaced_not_rendered(
    root_capture, bound_guard
) -> None:
    _ars_logger().error("needs %d args %d", 1)

    assert safe_logging.UNFORMATTABLE_RECORD_REPLACED in root_capture.text()


# -- rule 2b: dependency records are replaced wholesale --------------------


def test_dependency_record_in_run_context_is_replaced_wholesale(
    root_capture, bound_guard
) -> None:
    # The SDK's message layout is not ARS's to reason about: a value can sit in
    # a field ARS has never seen, so the whole payload goes and only the
    # categorical fact plus the logger family survives.
    _dependency_logger().error("validation failed: input_value=%s", SECRET)

    text = root_capture.text()
    assert SECRET not in text
    assert safe_logging.DEPENDENCY_RECORD_REPLACED in text
    assert "logger=acp" in text
    assert "validation failed" not in text


def test_root_logged_dependency_record_is_replaced_too(
    root_capture, bound_guard
) -> None:
    logging.getLogger().error("module-level sdk log %s", SECRET)

    assert SECRET not in root_capture.text()
    assert safe_logging.DEPENDENCY_RECORD_REPLACED in root_capture.text()


# -- rule 3: Run-tagged but unguarded is suppressed ------------------------


def test_run_tagged_record_without_a_guard_is_suppressed(root_capture) -> None:
    # Which Run's values could be in this text is unknowable, so the payload
    # goes rather than being guessed at.
    _ars_logger().error("late diagnostic %s", SECRET, extra={"run_id": "run-log-9"})

    text = root_capture.text()
    assert SECRET not in text
    assert safe_logging.UNSANITIZED_RUN_LOG_SUPPRESSED in text


# -- reviewer note 3: contextvars do not cross a thread boundary -----------


def test_off_thread_record_is_still_guarded_while_a_run_is_live(
    root_capture, bound_guard
) -> None:
    """A raw thread inherits no context, so context alone is not the model.

    The process-wide registry of live guards closes it structurally: while any
    Run is live, an untagged off-context record is guarded against every live
    guard rather than trusted.
    """
    seen: dict[str, object] = {}

    def worker() -> None:
        seen["guard"] = safe_logging.current_run_guard()
        seen["run_id"] = safe_logging.current_run_id()
        _ars_logger().error("worker thread saw %s", SECRET)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(10)

    # The premise of the note, asserted rather than assumed.
    assert seen["guard"] is None
    assert seen["run_id"] is None
    assert SECRET not in root_capture.text()
    assert ENV_VALUE_REPLACEMENT in root_capture.text()


def test_off_thread_dependency_record_is_replaced_while_a_run_is_live(
    root_capture, bound_guard
) -> None:
    def worker() -> None:
        _dependency_logger().error("sdk thread %s", SECRET)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(10)

    assert SECRET not in root_capture.text()
    assert safe_logging.DEPENDENCY_RECORD_REPLACED in root_capture.text()


def test_the_registry_empties_when_the_binding_is_released() -> None:
    guard = _guard()
    binding = safe_logging.bind_run_guard(guard, run_id="run-log-2")
    assert guard in safe_logging.active_guards()

    safe_logging.unbind_run_guard(binding)

    assert guard not in safe_logging.active_guards()
    assert safe_logging.current_run_guard() is None
    assert safe_logging.current_run_id() is None


def test_unbinding_twice_is_harmless() -> None:
    guard = _guard()
    binding = safe_logging.bind_run_guard(guard, run_id="run-log-3")
    safe_logging.unbind_run_guard(binding)
    safe_logging.unbind_run_guard(binding)

    assert safe_logging.active_guards() == ()


# -- installation ----------------------------------------------------------


def test_install_covers_every_root_handler_and_last_resort(root_capture) -> None:
    extra = _Capture()
    logging.getLogger().addHandler(extra)
    try:
        # A handler added after the first install is covered by the next one,
        # which every guard binding performs.
        assert not any(item is safe_logging._FILTER for item in extra.filters)
        safe_logging.install_safe_logging()
        assert any(item is safe_logging._FILTER for item in extra.filters)
        assert any(
            item is safe_logging._FILTER for item in logging.lastResort.filters
        )
        assert safe_logging.safe_logging_installed()
    finally:
        logging.getLogger().removeHandler(extra)


def test_install_is_idempotent_by_filter_identity(root_capture) -> None:
    before = list(root_capture.filters)
    safe_logging.install_safe_logging()
    safe_logging.install_safe_logging()

    assert list(root_capture.filters) == before


def test_binding_a_guard_installs_the_containment_first() -> None:
    extra = _Capture()
    logging.getLogger().addHandler(extra)
    guard = _guard()
    try:
        assert not any(item is safe_logging._FILTER for item in extra.filters)
        binding = safe_logging.bind_run_guard(guard, run_id="run-log-4")
        try:
            assert any(item is safe_logging._FILTER for item in extra.filters)
        finally:
            safe_logging.unbind_run_guard(binding)
    finally:
        logging.getLogger().removeHandler(extra)


def test_the_filter_never_drops_a_record(root_capture, bound_guard) -> None:
    _ars_logger().error("kept %s", SECRET)

    assert root_capture.records, "containment is replacement, never silence"


# -- the daemon refuses to serve without containment ----------------------


def test_server_refuses_to_listen_when_containment_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(safe_logging, "safe_logging_installed", lambda: False)

    policy = server.CallerPolicy(
        {
            1000: server.Principal(
                principal_id="p1", owner_namespaces=frozenset({("o", "n")})
            )
        }
    )

    async def handler(caller, request):  # pragma: no cover - never reached
        raise AssertionError("the server must refuse before serving")

    instance = server.ArsdServer(
        socket_path=tmp_path / "arsd.sock", policy=policy, handler=handler
    )

    with pytest.raises(server.ServerStartupError):
        asyncio.run(instance.start())

    assert not (tmp_path / "arsd.sock").exists()

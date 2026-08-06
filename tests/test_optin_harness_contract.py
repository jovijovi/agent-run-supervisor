"""The opt-in real-agent harnesses must speak the contract they advertise.

These suites are skipped by ``make verify`` — they need a live agent or a live
socket — so nothing else in the repository notices when the wire moves under
them. That is exactly how a harness rots into a false promise: it still claims
to prove the real-agent path, and it would fail at the first frame.

So the *request shapes and orchestration* of those harnesses are exercised here,
by default, with no agent and no socket. This does not run the canaries. It runs
the builders and the sequencing they use, and holds them to the v3 contract:
select ``agent_id``, omit ``session_id`` on the first Run, and reuse the id that
Run actually returned.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from agent_run_supervisor.arsd import protocol

pytest.importorskip("acp")

import tests.arsd.test_real_socket_acceptance as socket_acceptance
import tests.native_acp.test_real_opencode_smoke as opencode_smoke


# -- the native opt-in smoke -------------------------------------------------


def test_the_native_smoke_builds_a_create_request_with_no_session_id() -> None:
    """The first Run of a real smoke has no Session to name yet."""
    request = opencode_smoke._request(
        model="provider/model", effort="high", session_id=None
    )
    assert request.session_id is None
    assert request.agent_id
    assert not hasattr(request, "profile_id")


def test_the_native_smoke_builds_a_reuse_request_from_a_returned_id() -> None:
    request = opencode_smoke._request(
        model="provider/model", effort="high", session_id="sess-from-run-1"
    )
    assert request.session_id == "sess-from-run-1"


def test_the_native_smoke_never_names_a_profile_or_a_reuse_mode() -> None:
    source = Path(opencode_smoke.__file__).read_text(encoding="utf-8")
    for retired in ("profile_id=", "session_reuse", "ars_session_id"):
        assert retired not in source, retired


def test_both_suites_continue_run_one_rather_than_inventing_an_id() -> None:
    """Structural: a multi-Run leg must consume its own first Run's identity.

    An invented Session id is the exact failure this guards: a leg that names a
    Session nothing created fails at admission, so it can never have proven the
    continuity it advertises.
    """
    native = inspect.getsource(opencode_smoke)
    assert "session_id = r1.session_id" in native, (
        "the native smoke must reuse the Session identity its first Run returned"
    )
    assert "smoke-s1" not in native and "smoke-s2" not in native, (
        "an invented Session id survives in the native smoke"
    )

    socket = inspect.getsource(socket_acceptance)
    assert 'session_id = a["session_id"]' in socket, (
        "the socket suite must reuse the Session identity its first Run returned"
    )
    assert "arsd-accept-s" not in socket, (
        "an invented Session id survives in the socket suite"
    )


# -- the arsd socket acceptance ----------------------------------------------


@pytest.fixture()
def acceptance_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The env the socket builder reads. No socket, no daemon, no agent."""
    for name, value in (
        ("ARS_ARSD_ACCEPTANCE_OWNER", "hermes"),
        ("ARS_ARSD_ACCEPTANCE_NAMESPACE", "hermes/acceptance"),
        ("ARS_ARSD_ACCEPTANCE_AGENT_ID", "acceptance-agent"),
        ("ARS_ARSD_ACCEPTANCE_WORKSPACE", "/tmp/acceptance-ws"),
    ):
        monkeypatch.setenv(name, value)


def test_the_socket_payload_builder_omits_session_id_for_a_create(
    acceptance_env,
) -> None:
    payload = socket_acceptance._request_payload(
        model="provider/model", effort="high", session_id=None
    )
    assert "session_id" not in payload, "a create omits the field entirely"
    assert payload["agent_id"]
    assert "profile_id" not in payload


def test_the_socket_payload_builder_carries_a_returned_id_for_a_reuse(
    acceptance_env,
) -> None:
    payload = socket_acceptance._request_payload(
        model="provider/model", effort="high", session_id="sess-from-run-1"
    )
    assert payload["session_id"] == "sess-from-run-1"


def test_the_socket_payload_parses_under_the_production_v3_parser(
    acceptance_env,
) -> None:
    """The strongest available check without a socket: the real parser."""
    payload = socket_acceptance._request_payload(
        model="provider/model", effort="high", session_id=None
    )
    command = protocol.parse_submit(
        {
            "request": payload,
            "prompt_text": "hello",
            "workspace_root": "/tmp/ws",
        }
    )
    assert command.request.session_id is None
    assert command.request.agent_id


def test_the_socket_suite_expects_only_the_served_api_version() -> None:
    """No pinned old version, and the live-ness check reads the constant.

    Reading the constant is stronger than pinning a literal: the assertion
    cannot go stale the next time the wire moves.
    """
    source = Path(socket_acceptance.__file__).read_text(encoding="utf-8")
    assert 'info["api_version"] == 1' not in source
    assert 'info["api_version"] == 2' not in source
    assert 'info["api_version"] == protocol.ARSD_API_VERSION' in source


def test_neither_opt_in_suite_still_speaks_a_retired_wire() -> None:
    for module in (opencode_smoke, socket_acceptance):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for retired in ('"profile_id"', "session_reuse", "ars_session_id"):
            assert retired not in source, f"{module.__name__}: {retired}"

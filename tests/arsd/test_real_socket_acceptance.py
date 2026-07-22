"""Stage-2 C-grade S1/S2/S3/S5 socket acceptance (A4-gated; skip-by-default).

Skips unless ``ARS_ARSD_SOCKET_ACCEPTANCE=1`` **and** the required test-scoped
policy/config inputs are present. Collection and the default skip path must not
launch a daemon/AGENT, inspect credentials, mutate service state, or incur
model calls. Preflight runs only from test bodies after A4 opt-in.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import time
from pathlib import Path

import pytest

_GATE = "ARS_ARSD_SOCKET_ACCEPTANCE"
_REQUIRED_ENV = (
    "ARS_ARSD_ACCEPTANCE_SOCKET",
    "ARS_ARSD_ACCEPTANCE_SUPERVISOR_ROOT",
    "ARS_ARSD_ACCEPTANCE_WORKSPACE",
    "ARS_ARSD_ACCEPTANCE_OWNER",
    "ARS_ARSD_ACCEPTANCE_NAMESPACE",
    "ARS_ARSD_ACCEPTANCE_CALLER_MAPPING",
    "ARS_ARSD_ACCEPTANCE_DENY_SOCKET",
)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WRITE_LIKE_OPS = frozenset(
    {
        "fs_write",
        "permission:edit",
        "permission:delete",
        "permission:move",
        "permission:execute",
    }
)


def _acceptance_ready() -> bool:
    if os.environ.get(_GATE) != "1":
        return False
    return all(os.environ.get(name) for name in _REQUIRED_ENV)


pytestmark = pytest.mark.skipif(
    not _acceptance_ready(),
    reason=(
        "opt-in arsd socket acceptance; set ARS_ARSD_SOCKET_ACCEPTANCE=1 "
        "plus required test-scoped socket/root/workspace/owner/namespace/"
        "caller-mapping/deny-socket inputs (A4 only)"
    ),
)

from agent_run_supervisor.arsd import client as arsd_client
from agent_run_supervisor.arsd import protocol
from agent_run_supervisor.native_acp import storage

REQUIRED_MODEL = "kimi-for-coding/k3"
REQUIRED_EFFORT = "max"
SECOND_MODEL = "deepseek/deepseek-v4-pro"
SECOND_EFFORT = "high"

RUN_TIMEOUT_SECONDS = 900
POLL_INTERVAL = 0.5


def _env(name: str) -> str:
    value = os.environ.get(name)
    assert value, f"missing required acceptance env: {name}"
    return value


def _socket_path() -> Path:
    return Path(_env("ARS_ARSD_ACCEPTANCE_SOCKET"))


def _supervisor_root() -> Path:
    return Path(_env("ARS_ARSD_ACCEPTANCE_SUPERVISOR_ROOT"))


def _workspace() -> Path:
    return Path(_env("ARS_ARSD_ACCEPTANCE_WORKSPACE"))


def _owner() -> str:
    return _env("ARS_ARSD_ACCEPTANCE_OWNER")


def _namespace() -> str:
    return _env("ARS_ARSD_ACCEPTANCE_NAMESPACE")


def _caller_mapping() -> str:
    return _env("ARS_ARSD_ACCEPTANCE_CALLER_MAPPING")


def _deny_socket() -> Path:
    return Path(_env("ARS_ARSD_ACCEPTANCE_DENY_SOCKET"))


def _resolve(path: Path) -> Path:
    return path.resolve(strict=False)


def _forbid_inside_repo(path: Path, *, label: str) -> None:
    resolved = _resolve(path)
    repo = _REPO_ROOT.resolve()
    try:
        resolved.relative_to(repo)
    except ValueError:
        return
    pytest.fail(f"{label} must not be inside the repository/worktree")


def _a4_preflight() -> None:
    """Inert structural preflight — call only from test bodies after A4 opt-in."""
    mapping = _caller_mapping()
    parts = mapping.split(":", 3)
    assert len(parts) == 4 and all(parts), "caller mapping shape invalid"
    assert int(parts[0]) == os.getuid(), "caller mapping UID must equal current uid"
    assert parts[2] == _owner(), "caller mapping owner must equal env owner"
    assert parts[3] == _namespace(), "caller mapping namespace must equal env namespace"

    sock = _socket_path()
    root = _supervisor_root()
    workspace = _workspace()
    deny = _deny_socket()
    for label, path in (
        ("socket", sock),
        ("supervisor_root", root),
        ("workspace", workspace),
        ("deny_socket", deny),
    ):
        assert path.is_absolute(), f"{label} must be absolute"
        _forbid_inside_repo(path, label=label)

    assert workspace.is_dir(), "workspace must exist"
    assert sorted(entry.name for entry in workspace.iterdir()) == [], (
        "workspace must be known-empty"
    )

    evidence_dir = os.environ.get("ARS_ARSD_ACCEPTANCE_EVIDENCE_DIR")
    if evidence_dir:
        evidence = Path(evidence_dir)
        assert evidence.is_absolute(), "evidence dir must be absolute"
        _forbid_inside_repo(evidence, label="evidence_dir")
        er, rr, wr = _resolve(evidence), _resolve(root), _resolve(workspace)
        assert er != rr and er != wr
        for other in (rr, wr):
            try:
                er.relative_to(other)
            except ValueError:
                pass
            else:
                pytest.fail("evidence_dir must not nest under supervisor_root/workspace")
            try:
                other.relative_to(er)
            except ValueError:
                pass
            else:
                pytest.fail("supervisor_root/workspace must not nest under evidence_dir")


def _mapping_policy_summary() -> dict:
    raw = _caller_mapping()
    parts = raw.split(":", 3)
    return {
        "mapping_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "uid_matches_current": int(parts[0]) == os.getuid(),
        "owner_matches_env": parts[2] == _owner(),
        "namespace_matches_env": parts[3] == _namespace(),
    }


def _evidence(name: str, payload: dict) -> None:
    evidence_dir = os.environ.get("ARS_ARSD_ACCEPTANCE_EVIDENCE_DIR")
    if not evidence_dir:
        return
    target = Path(evidence_dir)
    target.mkdir(parents=True, exist_ok=True)
    # Structural summaries only — never raw mapping, model text, credentials, env.
    body = dict(payload)
    body["caller_policy"] = _mapping_policy_summary()
    (target / f"{name}.json").write_text(
        json.dumps(body, indent=2, sort_keys=True), encoding="utf-8"
    )


def _wire_request(
    *,
    model: str,
    effort: str,
    session_id: str,
    grant_capabilities: tuple[str, ...] = ("read",),
    credential_refs: tuple[str, ...] = ("kimi-for-coding", "deepseek"),
    profile_id: str = "opencode-1.18.4",
) -> dict:
    return {
        "owner": _owner(),
        "namespace": _namespace(),
        "profile_id": profile_id,
        "session_reuse": "reuse",
        "ars_session_id": session_id,
        "expected_binding_hash": None,
        "input_refs": [
            {"ref": "prompt:inline", "content_hash": "sha256:" + "0" * 64},
        ],
        "requested_model": model,
        "requested_effort": effort,
        "grant_ref": "grant:arsd-socket-acceptance",
        "grant_hash": "sha256:" + "1" * 64,
        "grant_role_hash": "sha256:" + "2" * 64,
        "grant_capabilities": list(grant_capabilities),
        "mcp_snapshot_hashes": [],
        "credential_refs": list(credential_refs),
        "limits": {},
        "evidence_policy_hash": "sha256:" + "3" * 64,
        "recovery_policy_hash": "sha256:" + "4" * 64,
    }


def _submit_payload(
    *,
    request: dict,
    prompt_text: str,
    workspace_root: str | None = None,
) -> dict:
    return {
        "request": request,
        "prompt_text": prompt_text,
        "workspace_root": workspace_root or str(_workspace()),
        "cwd": None,
        "retry_of_run_id": None,
    }


def _run_dir(run_id: str) -> Path:
    return Path(storage.native_event_store(_supervisor_root()).base_dir) / run_id


def _wait_result(cli: arsd_client.ArsdClient, run_id: str) -> dict:
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = cli.run_status(run_id)
        if "result" in status:
            return status["result"]
        time.sleep(POLL_INTERVAL)
    pytest.fail(f"run {run_id} did not reach a terminal result within bound")


def _effective(run_dir: Path) -> dict:
    return json.loads((run_dir / "effective.json").read_text(encoding="utf-8"))


def _spec(run_dir: Path) -> dict:
    return json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))


def _events(run_dir: Path) -> list[dict]:
    path = run_dir / "events.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _assert_workspace_empty(workspace: Path) -> None:
    assert sorted(entry.name for entry in workspace.iterdir()) == []


def _assert_spec_effective_exact(run_dir: Path, model: str, effort: str) -> None:
    spec = _spec(run_dir)
    effective = _effective(run_dir)
    assert effective["effective_model"] == model
    assert effective["effective_effort"] == effort
    assert spec["runtime"]["model_id"] == effective["effective_model"]
    assert spec["runtime"]["effort"] == effective["effective_effort"]
    assert spec["runtime"]["config_fidelity"] == "exact"


def _assert_write_once_artifacts(run_dir: Path) -> None:
    for name in (
        "submission.json",
        "spec.json",
        "launch.json",
        "prompt-dispatch-started",
        "prompt-accepted",
        "result.json",
    ):
        assert (run_dir / name).exists(), name


def _assert_monotonic_unique_seq(events: list[dict]) -> None:
    seqs = [event["seq"] for event in events if "seq" in event]
    assert seqs, "events must carry seq"
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))


def _assert_session_load_only(run_dir: Path) -> None:
    families = [e["type"] for e in _events(run_dir)]
    assert "session_load_requested" in families
    assert "session_new_requested" not in families


def _recv_error_frame(raw: socket.socket) -> dict:
    raw.settimeout(5)
    buf = bytearray()
    while b"\n" not in buf:
        chunk = raw.recv(65536)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > protocol.MAX_FRAME_BYTES + 64:
            break
    line = bytes(buf.split(b"\n", 1)[0] + b"\n") if buf else b""
    assert line, "expected an error frame from arsd"
    frame = protocol.decode_frame(line)
    assert "error" in frame
    return frame


def test_s1_readonly_success_through_socket() -> None:
    _a4_preflight()
    workspace = _workspace()
    _assert_workspace_empty(workspace)
    marker = "ARS_ARSD_S1_OK"
    request_id = "arsd-s1-" + secrets.token_hex(4)
    session_id = "arsd-accept-s1-" + secrets.token_hex(4)
    payload = _submit_payload(
        request=_wire_request(
            model=REQUIRED_MODEL,
            effort=REQUIRED_EFFORT,
            session_id=session_id,
        ),
        prompt_text=(
            f"Reply with exactly {marker} and nothing else. "
            "Do not use any tools. Do not read or write any files."
        ),
    )
    with arsd_client.ArsdClient(_socket_path()) as cli:
        accepted = cli.submit(request_id=request_id, payload=payload)
        run_id = accepted["run_id"]
        result = _wait_result(cli, run_id)

    assert result["status"] == "completed"
    assert result.get("stop_reason") == "end_turn"
    assert result.get("retryable") is False
    assert marker in str(result.get("final_message", ""))

    run_dir = _run_dir(run_id)
    _assert_write_once_artifacts(run_dir)
    _assert_spec_effective_exact(run_dir, REQUIRED_MODEL, REQUIRED_EFFORT)
    events = _events(run_dir)
    _assert_monotonic_unique_seq(events)
    redaction = json.loads((run_dir / "redaction-report.json").read_text())
    assert redaction.get("matches") == []
    _assert_workspace_empty(workspace)

    before_bytes = {
        name: (run_dir / name).read_bytes()
        for name in (
            "submission.json",
            "spec.json",
            "launch.json",
            "result.json",
            "prompt-dispatch-started",
            "prompt-accepted",
        )
    }
    prompt_sent_before = sum(
        1 for e in events if e.get("type") == "session_prompt_sent"
    )
    with arsd_client.ArsdClient(_socket_path()) as cli:
        again = cli.submit(request_id=request_id, payload=payload)
    assert again["run_id"] == run_id
    for name, blob in before_bytes.items():
        assert (run_dir / name).read_bytes() == blob
    events_after = _events(run_dir)
    prompt_sent_after = sum(
        1 for e in events_after if e.get("type") == "session_prompt_sent"
    )
    assert prompt_sent_after == prompt_sent_before == 1
    _assert_workspace_empty(workspace)

    _evidence(
        "s1-readonly",
        {
            "run_id": run_id,
            "status": "completed",
            "stop_reason": "end_turn",
            "retryable": False,
            "final_message_matches_marker": True,
            "effective_model": REQUIRED_MODEL,
            "effective_effort": REQUIRED_EFFORT,
            "config_fidelity_exact": True,
            "artifacts_write_once": True,
            "dispatch_markers_present": True,
            "redaction_matches_empty": True,
            "workspace_empty_before_after": True,
            "events_monotonic_unique": True,
            "retransmit_same_identity": True,
            "retransmit_artifacts_unchanged": True,
            "retransmit_no_second_dispatch": True,
        },
    )


def test_s2_denied_action_canary_through_socket() -> None:
    _a4_preflight()
    workspace = _workspace()
    _assert_workspace_empty(workspace)
    sentinel = workspace / "ARS_ARSD_S2_SENTINEL_MUST_NOT_EXIST"
    request_id = "arsd-s2-" + secrets.token_hex(4)
    session_id = "arsd-accept-s2-" + secrets.token_hex(4)
    with arsd_client.ArsdClient(_socket_path()) as cli:
        accepted = cli.submit(
            request_id=request_id,
            payload=_submit_payload(
                request=_wire_request(
                    model=REQUIRED_MODEL,
                    effort=REQUIRED_EFFORT,
                    session_id=session_id,
                    grant_capabilities=("read",),
                ),
                prompt_text=(
                    "Create a new file named ARS_ARSD_S2_SENTINEL_MUST_NOT_EXIST "
                    "in the workspace and write the text DENIED_CANARY into it. "
                    "If you cannot write, say so briefly."
                ),
            ),
        )
        run_id = accepted["run_id"]
        _wait_result(cli, run_id)

    run_dir = _run_dir(run_id)
    events = _events(run_dir)
    mediation = [e for e in events if e.get("type") == "permission_mediation"]
    assert mediation, "zero permission_mediation events = FAIL (G10)"
    write_denies = [
        e
        for e in mediation
        if e.get("decision") == "deny" and e.get("requested_op") in _WRITE_LIKE_OPS
    ]
    assert write_denies, (
        "expected at least one deny whose requested_op is a write/edit family"
    )
    assert not sentinel.exists()
    _assert_workspace_empty(workspace)
    _evidence(
        "s2-denied-canary",
        {
            "run_id": run_id,
            "mediation_events": len(mediation),
            "write_like_deny_observed": True,
            "denied_ops": sorted(
                {e.get("requested_op") for e in write_denies if e.get("requested_op")}
            ),
            "sentinel_absent": True,
            "workspace_empty_before_after": True,
        },
    )


def test_s3_session_continuity_and_model_switch_through_socket() -> None:
    _a4_preflight()
    workspace = _workspace()
    _assert_workspace_empty(workspace)
    nonce = "NONCE-" + secrets.token_hex(8)
    session_id = "arsd-accept-s3-" + secrets.token_hex(6)
    store = storage.native_session_store(_supervisor_root())

    with arsd_client.ArsdClient(_socket_path()) as cli:
        # A: K3/max plants nonce
        a = cli.submit(
            request_id="arsd-s3a-" + secrets.token_hex(3),
            payload=_submit_payload(
                request=_wire_request(
                    model=REQUIRED_MODEL,
                    effort=REQUIRED_EFFORT,
                    session_id=session_id,
                ),
                prompt_text=(
                    f"Remember this token exactly: {nonce}. "
                    "Reply with exactly STORED and nothing else. Do not use any tools."
                ),
            ),
        )
        run_a = a["run_id"]
        result_a = _wait_result(cli, run_a)
        assert result_a["status"] == "completed"
        assert "STORED" in str(result_a.get("final_message", ""))
        _assert_spec_effective_exact(_run_dir(run_a), REQUIRED_MODEL, REQUIRED_EFFORT)
        record_a = store.open_session(session_id)
        external_id = record_a.agent_session_id
        assert external_id
        assert (record_a.last_effective_model, record_a.last_effective_effort) == (
            REQUIRED_MODEL,
            REQUIRED_EFFORT,
        )

        # B: K3/max load + recall
        b = cli.submit(
            request_id="arsd-s3b-" + secrets.token_hex(3),
            payload=_submit_payload(
                request=_wire_request(
                    model=REQUIRED_MODEL,
                    effort=REQUIRED_EFFORT,
                    session_id=session_id,
                ),
                prompt_text=(
                    "Reply with the exact token I asked you to remember earlier "
                    "in this conversation, and nothing else. Do not use any tools."
                ),
            ),
        )
        run_b = b["run_id"]
        result_b = _wait_result(cli, run_b)
        assert result_b["status"] == "completed"
        assert nonce in str(result_b.get("final_message", ""))
        _assert_spec_effective_exact(_run_dir(run_b), REQUIRED_MODEL, REQUIRED_EFFORT)
        _assert_session_load_only(_run_dir(run_b))
        record_b = store.open_session(session_id)
        assert record_b.agent_session_id == external_id

        # C: second model/effort load
        c = cli.submit(
            request_id="arsd-s3c-" + secrets.token_hex(3),
            payload=_submit_payload(
                request=_wire_request(
                    model=SECOND_MODEL,
                    effort=SECOND_EFFORT,
                    session_id=session_id,
                ),
                prompt_text=(
                    "Reply with exactly S3_SWITCH_OK and nothing else. "
                    "Do not use any tools."
                ),
            ),
        )
        run_c = c["run_id"]
        result_c = _wait_result(cli, run_c)
        assert result_c["status"] == "completed"
        assert "S3_SWITCH_OK" in str(result_c.get("final_message", ""))
        _assert_spec_effective_exact(_run_dir(run_c), SECOND_MODEL, SECOND_EFFORT)
        _assert_session_load_only(_run_dir(run_c))
        record_c = store.open_session(session_id)
        assert record_c.agent_session_id == external_id
        assert (record_c.last_effective_model, record_c.last_effective_effort) == (
            SECOND_MODEL,
            SECOND_EFFORT,
        )

        # D: switch back to K3/max and load
        d = cli.submit(
            request_id="arsd-s3d-" + secrets.token_hex(3),
            payload=_submit_payload(
                request=_wire_request(
                    model=REQUIRED_MODEL,
                    effort=REQUIRED_EFFORT,
                    session_id=session_id,
                ),
                prompt_text=(
                    "Reply with exactly S3_SWITCHBACK_OK and nothing else. "
                    "Do not use any tools."
                ),
            ),
        )
        run_d = d["run_id"]
        result_d = _wait_result(cli, run_d)
        assert result_d["status"] == "completed"
        assert "S3_SWITCHBACK_OK" in str(result_d.get("final_message", ""))
        _assert_spec_effective_exact(_run_dir(run_d), REQUIRED_MODEL, REQUIRED_EFFORT)
        _assert_session_load_only(_run_dir(run_d))
        record_d = store.open_session(session_id)
        assert record_d.agent_session_id == external_id
        assert (record_d.last_effective_model, record_d.last_effective_effort) == (
            REQUIRED_MODEL,
            REQUIRED_EFFORT,
        )

    _assert_workspace_empty(workspace)
    exact_readback = True
    switchback = True
    _evidence(
        "s3-continuity-switch",
        {
            "nonce_recalled": True,
            "external_session_id_unchanged": True,
            "b_c_d_used_session_load": True,
            "second_model": SECOND_MODEL,
            "second_effort": SECOND_EFFORT,
            "last_effective_after_c": [SECOND_MODEL, SECOND_EFFORT],
            "last_effective_after_d": [REQUIRED_MODEL, REQUIRED_EFFORT],
            "exact_readback": exact_readback,
            "switchback": switchback,
            "workspace_empty_before_after": True,
            "run_ids": [run_a, run_b, run_c, run_d],
        },
    )


def test_s5_malformed_failing_isolation_then_success() -> None:
    _a4_preflight()
    workspace = _workspace()
    _assert_workspace_empty(workspace)
    sock = _socket_path()
    deny = _deny_socket()

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
        raw.connect(str(sock))
        raw.sendall(b"{not-json\n")
        frame = _recv_error_frame(raw)
    assert frame["error"]["code"] == protocol.MALFORMED_FRAME

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
        raw.connect(str(sock))
        raw.sendall(b"x" * (protocol.MAX_FRAME_BYTES + 8) + b"\n")
        frame = _recv_error_frame(raw)
    assert frame["error"]["code"] == protocol.FRAME_TOO_LARGE

    with pytest.raises(arsd_client.ArsdPeerUidDeniedError):
        with arsd_client.ArsdClient(deny) as cli:
            cli.server_info(request_id="deny-probe-" + secrets.token_hex(2))

    # Outside-domain model: durable submit ACK precedes RunTask profile/config
    # admission, so expect accepted then pre-prompt terminal failed/unknown.
    bad_req = "arsd-s5-bad-" + secrets.token_hex(4)
    bad_sess = "arsd-accept-s5-bad-" + secrets.token_hex(4)
    with arsd_client.ArsdClient(sock) as cli:
        accepted = cli.submit(
            request_id=bad_req,
            payload=_submit_payload(
                request=_wire_request(
                    model="not-a-registered-model/zzz",
                    effort=REQUIRED_EFFORT,
                    session_id=bad_sess,
                ),
                prompt_text="should not run",
            ),
        )
        bad_run_id = accepted["run_id"]
        bad_result = _wait_result(cli, bad_run_id)
    assert bad_result["status"] in {"failed", "unknown"}
    bad_events = _events(_run_dir(bad_run_id))
    assert sum(1 for e in bad_events if e.get("type") == "session_prompt_sent") == 0

    key = "arsd-s5-idem-" + secrets.token_hex(4)
    idem_sess = "arsd-accept-s5-idem-" + secrets.token_hex(4)
    idem_payload = _submit_payload(
        request=_wire_request(
            model=REQUIRED_MODEL,
            effort=REQUIRED_EFFORT,
            session_id=idem_sess,
        ),
        prompt_text="Reply with exactly S5_IDEM_A and nothing else.",
    )
    with arsd_client.ArsdClient(sock) as cli:
        first = cli.submit(request_id=key, payload=idem_payload)
        run_id = first["run_id"]
    with arsd_client.ArsdClient(sock) as cli:
        with pytest.raises(arsd_client.ArsdIdempotencyConflictError):
            cli.submit(
                request_id=key,
                payload=_submit_payload(
                    request=_wire_request(
                        model=REQUIRED_MODEL,
                        effort=REQUIRED_EFFORT,
                        session_id=idem_sess,
                    ),
                    prompt_text="Reply with exactly S5_IDEM_B and nothing else.",
                ),
            )

    with arsd_client.ArsdClient(sock) as cli:
        again = cli.submit(request_id=key, payload=idem_payload)
        assert again["run_id"] == run_id
        result = _wait_result(cli, run_id)
    assert result["status"] == "completed"
    events = _events(_run_dir(run_id))
    assert sum(1 for e in events if e.get("type") == "session_prompt_sent") == 1

    # Controllable pre-dispatch failure: unknown profile (not request credential_refs;
    # spawn env comes from profile env_allowlist).
    with arsd_client.ArsdClient(sock) as cli:
        fail = cli.submit(
            request_id="arsd-s5-fail-" + secrets.token_hex(4),
            payload=_submit_payload(
                request=_wire_request(
                    model=REQUIRED_MODEL,
                    effort=REQUIRED_EFFORT,
                    session_id="arsd-accept-s5-fail-" + secrets.token_hex(4),
                    profile_id="profile-absent-for-acceptance",
                ),
                prompt_text="should fail closed before prompt",
            ),
        )
        fail_result = _wait_result(cli, fail["run_id"])
    assert fail_result["status"] in {"failed", "unknown"}
    fail_events = _events(_run_dir(fail["run_id"]))
    assert sum(1 for e in fail_events if e.get("type") == "session_prompt_sent") == 0

    ok_marker = "S5_OK"
    with arsd_client.ArsdClient(sock) as cli:
        ok = cli.submit(
            request_id="arsd-s5-ok-" + secrets.token_hex(4),
            payload=_submit_payload(
                request=_wire_request(
                    model=REQUIRED_MODEL,
                    effort=REQUIRED_EFFORT,
                    session_id="arsd-accept-s5-ok-" + secrets.token_hex(4),
                ),
                prompt_text=(
                    f"Reply with exactly {ok_marker} and nothing else. "
                    "Do not use any tools."
                ),
            ),
        )
        ok_result = _wait_result(cli, ok["run_id"])
    assert ok_result["status"] == "completed"
    assert ok_marker in str(ok_result.get("final_message", ""))
    _assert_spec_effective_exact(_run_dir(ok["run_id"]), REQUIRED_MODEL, REQUIRED_EFFORT)
    _assert_workspace_empty(workspace)

    with arsd_client.ArsdClient(sock) as probe:
        info = probe.server_info(request_id="s5-alive-" + secrets.token_hex(2))
        assert info["api_version"] == 1

    _evidence(
        "s5-isolation",
        {
            "malformed_code": protocol.MALFORMED_FRAME,
            "oversize_code": protocol.FRAME_TOO_LARGE,
            "peer_uid_denied": True,
            "outside_domain_pre_prompt_terminal": True,
            "idempotency_conflict": True,
            "duplicate_no_redispatch": True,
            "failing_run_isolated": True,
            "subsequent_success": True,
            "subsequent_marker_matched": True,
            "workspace_empty_before_after": True,
            "primary_model": REQUIRED_MODEL,
            "primary_effort": REQUIRED_EFFORT,
        },
    )

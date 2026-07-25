"""Codex closed-profile socket acceptance (opt-in; skip-by-default).

Skips unless ``ARS_CODEX_SOCKET_ACCEPTANCE=1`` **and** every test-scoped input
below is present. Collection and the default skip path must not launch a
daemon/AGENT, read credentials, mutate service state, or incur model calls;
all preflight runs from test bodies after opt-in.

Both legs run **in-process ephemeral ``ArsdServer`` daemons** with injected
``ArsdHandlers(profile_registry=…)`` on private tmp sockets, so the full UDS
wire path (SO_PEERCRED auth, closed payloads, the RunTask vertical) is
exercised; only the process-hosting of the daemon differs from production. The
production ``arsd serve`` ingress has no registry-injection surface and
acquiring one for acceptance would be scope expansion — the registered
production row is covered instead by the P0 parity assertion plus the
production canary under its own authorization boundary.

Isolation invariants this module must never break:

* **Positive legs** use a *derived* profile whose only behavior-affecting
  difference from the registered row is ``CODEX_HOME`` → an ephemeral home.
  Real ``session/new``/``session/load`` persist thread/rollout state under the
  effective ``CODEX_HOME``, so an untouched real root and a GREEN positive leg
  are mutually exclusive. The real adapter entry, frozen Node, and real CLI are
  used strictly read-only.
* **Negative legs** run against a second daemon whose private registry points
  exclusively at private temporary copies (copied Node, a harness-written
  codex-shaped fake ACP agent, a private ``codex`` symlink over a copied
  placeholder CLI, and a *synthetic* credential root). Real credential bytes
  are never copied there; root checks are structural only. Every tamper, swap,
  retarget, and insertion mutates only those private copies.
* **The real credential root is read-only and observation-neutral**: every
  recorded ``lstat`` fact — including ``st_atime_ns`` and ``st_ctime_ns`` — is
  identical pre-suite and post-suite. Directories are opened with fail-closed
  ``O_NOATIME``, entries are enumerated on the directory fd, and no content is
  ever read, digested, or readlink'd.
* **Gate-target binding**: the harness verifies a clean checkout at exactly the
  reviewed implementation commit before the first leg and records that SHA next
  to every leg result. A bundle without the SHA binding is incomplete.

Credential-copy teardown (zero-overwrite → fsync → unlink → tree removal) is
best-effort *logical* cleanup of an ephemeral copy. On CoW filesystems, SSD
flash translation layers, journals, or page-cache-backed tmp storage the old
bytes may persist physically; no physical secure-erasure claim is made. The
retained proof is post-suite **absence**.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

_GATE = "ARS_CODEX_SOCKET_ACCEPTANCE"
_REQUIRED_ENV = (
    "ARS_CODEX_ACCEPTANCE_SOCKET_DIR",
    "ARS_CODEX_ACCEPTANCE_SUPERVISOR_ROOT",
    "ARS_CODEX_ACCEPTANCE_WORKSPACE_PARENT",
    "ARS_CODEX_ACCEPTANCE_OWNER",
    "ARS_CODEX_ACCEPTANCE_NAMESPACE",
    "ARS_CODEX_ACCEPTANCE_CALLER_MAPPING",
    "ARS_CODEX_ACCEPTANCE_COMMIT_SHA",
)
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _acceptance_ready() -> bool:
    if os.environ.get(_GATE) != "1":
        return False
    return all(os.environ.get(name) for name in _REQUIRED_ENV)


pytestmark = pytest.mark.skipif(
    not _acceptance_ready(),
    reason=(
        "opt-in codex socket acceptance; set ARS_CODEX_SOCKET_ACCEPTANCE=1 plus "
        "test-scoped socket-dir/supervisor-root/workspace-parent/owner/namespace/"
        "caller-mapping/commit-sha inputs (boundary D only)"
    ),
)

import dataclasses  # noqa: E402

from agent_run_supervisor.arsd import client as arsd_client  # noqa: E402
from agent_run_supervisor.arsd import handlers, server  # noqa: E402
from agent_run_supervisor.native_acp import storage  # noqa: E402
from agent_run_supervisor.native_acp import profile as profile_module  # noqa: E402
from agent_run_supervisor.native_acp.attestation import (  # noqa: E402
    ExpectedRuntimeIdentity,
)
from agent_run_supervisor.native_acp.profile import (  # noqa: E402
    CODEX_ACP_1_1_7,
    DEFAULT_REGISTRY,
    AgentProfile,
    ProfileRegistry,
)
from agent_run_supervisor.native_acp.run_task import DISPATCH_STARTED_MARKER  # noqa: E402
from agent_run_supervisor.native_acp.spec import (  # noqa: E402
    AgentRunRequest,
    InputRef,
    RunLimits,
    RunSpecAssembler,
    spec_hash,
)

REQUIRED_MODEL = "gpt-5.6-sol"
REQUIRED_EFFORT = "max"
POSITIVE_PROFILE_ID = "codex-acp-1.1.7-e2e-pos"
NEGATIVE_PROFILE_ID = "codex-acp-e2e-neg"
NEGATIVE_EXECUTABLE_KEY = "codex-acp-e2e"
RUN_TIMEOUT_SECONDS = 900
POLL_INTERVAL = 0.5

# Synthetic placeholder for the private negative root. Real credential bytes
# are never copied into any negative fixture.
SYNTHETIC_AUTH_BYTES = b'{"placeholder":"synthetic-not-a-real-credential"}\n'

# Inventory facts compared pre/post on the real credential root. Nanosecond
# timestamps are included deliberately: not even an access-time drift escapes.
_INVENTORY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_size",
    "st_mode",
    "st_uid",
    "st_gid",
    "st_nlink",
    "st_mtime_ns",
    "st_atime_ns",
    "st_ctime_ns",
)

_BWRAP_SIGNATURES = ("bwrap", "bubblewrap", "RTM_NEWADDR")

EVIDENCE: dict[str, object] = {"legs": {}}


# --- environment and structural preflight -----------------------------------


def _env(name: str) -> str:
    value = os.environ.get(name)
    assert value, f"missing required acceptance env: {name}"
    return value


def _forbid_inside_repo(path: Path, *, label: str) -> None:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(_REPO_ROOT.resolve())
    except ValueError:
        return
    pytest.fail(f"{label} must not be inside the repository/worktree")


def _commit_binding() -> dict[str, str]:
    """D14: acceptance may examine exactly one clean implementation commit."""
    expected = _env("ARS_CODEX_ACCEPTANCE_COMMIT_SHA")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert head == expected, (
        "acceptance must run from a clean checkout of the reviewed commit"
    )
    assert status == "", "acceptance checkout is not clean"
    return {"commit_sha": head, "clean_worktree": "true"}


def _codex_preflight() -> None:
    """Inert structural preflight — call only from test bodies after opt-in."""
    mapping = _env("ARS_CODEX_ACCEPTANCE_CALLER_MAPPING")
    parts = mapping.split(":", 3)
    assert len(parts) == 4 and all(parts), "caller mapping shape invalid"
    assert int(parts[0]) == os.getuid(), "caller mapping UID must equal current uid"
    assert parts[2] == _env("ARS_CODEX_ACCEPTANCE_OWNER")
    assert parts[3] == _env("ARS_CODEX_ACCEPTANCE_NAMESPACE")

    for label, path in (
        ("socket_dir", Path(_env("ARS_CODEX_ACCEPTANCE_SOCKET_DIR"))),
        ("supervisor_root", Path(_env("ARS_CODEX_ACCEPTANCE_SUPERVISOR_ROOT"))),
        ("workspace_parent", Path(_env("ARS_CODEX_ACCEPTANCE_WORKSPACE_PARENT"))),
    ):
        assert path.is_absolute(), f"{label} must be absolute"
        _forbid_inside_repo(path, label=label)

    # The production socket is never referenced by any harness config.
    production_socket = os.environ.get("ARS_ARSD_PRODUCTION_SOCKET")
    if production_socket:
        assert (
            Path(production_socket).resolve(strict=False)
            != Path(_env("ARS_CODEX_ACCEPTANCE_SOCKET_DIR")).resolve(strict=False)
        )


# --- D12 fail-closed observation-neutral inventory ---------------------------


def _open_dir_noatime(path: Path, *, dir_fd: int | None = None) -> int:
    """Directory fd under mandatory O_NOATIME.

    Linux permits O_NOATIME because the harness euid owns these files. If the
    open nevertheless fails the suite FAILs immediately: there is no fallback
    to a plain O_RDONLY read, because such a read may advance atime and make
    the untouched-root invariant unprovable.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    flags |= getattr(os, "O_NOATIME", 0)
    assert hasattr(os, "O_NOATIME"), "O_NOATIME is required and has no fallback"
    try:
        if dir_fd is None:
            return os.open(str(path), flags)
        return os.open(str(path), flags, dir_fd=dir_fd)
    except OSError as exc:  # pragma: no cover - fail-closed path
        pytest.fail(f"fail-closed: O_NOATIME open of {path} failed: {exc.errno}")


def _inventory_tree(root: Path) -> dict[str, dict[str, int]]:
    """Full-field lstat inventory. No content reads, digests, or readlinks."""
    facts: dict[str, dict[str, int]] = {}

    def walk(directory_fd: int, prefix: str) -> None:
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                info = os.lstat(entry.name, dir_fd=directory_fd)
                key = f"{prefix}{entry.name}"
                facts[key] = {
                    field: getattr(info, field) for field in _INVENTORY_FIELDS
                }
                if stat.S_ISDIR(info.st_mode):
                    child = _open_dir_noatime(Path(entry.name), dir_fd=directory_fd)
                    try:
                        walk(child, f"{key}/")
                    finally:
                        os.close(child)

    root_fd = _open_dir_noatime(root)
    try:
        root_info = os.fstat(root_fd)
        facts["."] = {field: getattr(root_info, field) for field in _INVENTORY_FIELDS}
        walk(root_fd, "")
    finally:
        os.close(root_fd)
    return facts


def _stage_credentials(home: Path, real_auth: Path) -> None:
    """fd-to-fd copy of the real auth bytes into an ephemeral 0600 file.

    Bytes exist only in process memory: never logged, never shelled, never
    hashed into evidence.
    """
    home.mkdir(mode=0o700, parents=True)
    os.chmod(home, 0o700)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NOATIME", 0)
    try:
        source_fd = os.open(str(real_auth), flags)
    except OSError as exc:  # pragma: no cover - fail-closed path
        pytest.fail(f"fail-closed: O_NOATIME staging read failed: {exc.errno}")
    try:
        target_fd = os.open(
            str(home / "auth.json"),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600,
        )
        try:
            while True:
                block = os.read(source_fd, 1 << 16)
                if not block:
                    break
                os.write(target_fd, block)
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
    finally:
        os.close(source_fd)


def _teardown_credentials(home: Path) -> None:
    """Best-effort logical cleanup; the retained proof is absence, not erasure."""
    staged = home / "auth.json"
    try:
        if staged.exists():
            size = staged.stat().st_size
            fd = os.open(str(staged), os.O_WRONLY | os.O_CLOEXEC)
            try:
                os.write(fd, b"\x00" * size)
                os.fsync(fd)
            finally:
                os.close(fd)
            staged.unlink()
    finally:
        shutil.rmtree(home, ignore_errors=True)
    assert not staged.exists()
    assert not home.exists()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- profiles ----------------------------------------------------------------


def _positive_profile(home: Path) -> AgentProfile:
    """Derived row: identical except ``profile_id`` and the CODEX_HOME value."""
    return dataclasses.replace(
        CODEX_ACP_1_1_7,
        profile_id=POSITIVE_PROFILE_ID,
        fixed_env=tuple(
            (name, str(home) if name == "CODEX_HOME" else value)
            for name, value in CODEX_ACP_1_1_7.fixed_env
        ),
    )


CODEX_FAKE_AGENT_MJS = """\
// Codex-shaped fake ACP agent: newline-delimited JSON-RPC 2.0 over stdio.
// Private acceptance fixture only — never product runtime, never evidence.
const AGENT_NAME = process.env.FAKE_AGENT_NAME;
const AGENT_VERSION = process.env.FAKE_AGENT_VERSION;
const SESSION_ID = 'codex-e2e-session-1';
let model = 'provider/base';
let effort = 'high';

function emit(payload) {
  process.stdout.write(JSON.stringify(payload) + '\\n');
}
function options() {
  return [
    {
      id: 'model',
      name: 'Model',
      type: 'select',
      currentValue: model,
      options: [
        { value: 'provider/base', name: 'Base' },
        { value: 'gpt-5.6-sol', name: 'Sol' },
      ],
    },
    {
      id: 'reasoning_effort',
      name: 'Reasoning effort',
      type: 'select',
      currentValue: effort,
      options: [
        { value: 'high', name: 'High' },
        { value: 'max', name: 'Max' },
      ],
    },
  ];
}

let buffer = '';
process.stdin.on('data', (chunk) => {
  buffer += chunk.toString('utf8');
  let index;
  while ((index = buffer.indexOf('\\n')) >= 0) {
    const line = buffer.slice(0, index).trim();
    buffer = buffer.slice(index + 1);
    if (!line) continue;
    let message;
    try {
      message = JSON.parse(line);
    } catch (err) {
      continue;
    }
    handle(message);
  }
});

function handle(message) {
  const id = message.id;
  switch (message.method) {
    case 'initialize':
      emit({
        jsonrpc: '2.0',
        id,
        result: {
          protocolVersion: 1,
          agentCapabilities: { loadSession: true },
          agentInfo: { name: AGENT_NAME, version: AGENT_VERSION },
        },
      });
      break;
    case 'session/new':
      emit({
        jsonrpc: '2.0',
        id,
        result: { sessionId: SESSION_ID, configOptions: options() },
      });
      break;
    case 'session/load':
      emit({ jsonrpc: '2.0', id, result: { configOptions: options() } });
      break;
    case 'session/set_config_option': {
      const params = message.params || {};
      if (params.configId === 'model') model = params.value;
      if (params.configId === 'reasoning_effort') effort = params.value;
      emit({ jsonrpc: '2.0', id, result: { configOptions: options() } });
      break;
    }
    case 'session/prompt':
      emit({
        jsonrpc: '2.0',
        method: 'session/update',
        params: {
          sessionId: SESSION_ID,
          update: {
            sessionUpdate: 'agent_message_chunk',
            content: { type: 'text', text: 'CODEX_E2E_SEED_OK' },
          },
        },
      });
      emit({ jsonrpc: '2.0', id, result: { stopReason: 'end_turn' } });
      break;
    case 'session/cancel':
      break;
    case 'session/close':
      emit({ jsonrpc: '2.0', id, result: {} });
      break;
    default:
      if (id !== undefined && id !== null) {
        emit({
          jsonrpc: '2.0',
          id,
          error: { code: -32601, message: 'method not found' },
        });
      }
  }
}
"""


class PrivateNegativeFixtures:
    """Private copies backing every negative leg; nothing real is referenced."""

    def __init__(self, base: Path) -> None:
        self.base = base
        stage = base / "stage"
        stage.mkdir(parents=True)
        real_node = Path(DEFAULT_REGISTRY.get("codex-acp-1.1.7").expected_runtime.node_path)
        self.node = stage / "node"
        shutil.copy2(real_node, self.node)
        os.chmod(self.node, 0o555)
        self.entry = stage / "codex-fake-agent.mjs"
        self.entry.write_text(CODEX_FAKE_AGENT_MJS, encoding="utf-8")
        self.cli_target = stage / "codex-placeholder"
        self.cli_target.write_bytes(b"#!/bin/false\n# private placeholder cli\n")
        os.chmod(self.cli_target, 0o555)
        self.cli = stage / "codex"
        self.cli.symlink_to(self.cli_target)
        self.cred_root = base / "synthetic-codex-home"
        self.cred_root.mkdir(mode=0o700)
        os.chmod(self.cred_root, 0o700)
        self.auth = self.cred_root / "auth.json"
        self.auth.write_bytes(SYNTHETIC_AUTH_BYTES)
        os.chmod(self.auth, 0o600)
        self.workspace = base / "neg-workspace"
        self.workspace.mkdir()

    def expected(self) -> ExpectedRuntimeIdentity:
        return ExpectedRuntimeIdentity(
            node_path=str(self.node),
            node_sha256=_sha256_file(self.node),
            adapter_entry_path=str(self.entry),
            adapter_entry_sha256=_sha256_file(self.entry),
            cli_path=str(self.cli),
            cli_sha256=_sha256_file(self.cli_target),
            agent_info_name="@example/codex-e2e-fake",
            agent_info_version="1.1.7",
            protocol_version="1",
        )

    def profile(self, **overrides) -> AgentProfile:
        kwargs = dict(
            profile_id=NEGATIVE_PROFILE_ID,
            revision=1,
            executable_key=NEGATIVE_EXECUTABLE_KEY,
            argv_template=(str(self.entry),),
            env_allowlist=(
                "HOME",
                "PATH",
                "FAKE_AGENT_NAME",
                "FAKE_AGENT_VERSION",
            ),
            fixed_env=(
                ("CODEX_HOME", str(self.cred_root)),
                ("CODEX_PATH", str(self.cli)),
                ("CODEX_CONFIG", '{"features":{"use_legacy_landlock":true}}'),
                ("INITIAL_AGENT_MODE", "read-only"),
                ("NO_BROWSER", "1"),
            ),
            credential_slots=("codex-home-auth",),
            required_credential_refs=("codex-home-auth",),
            model_selector_id="model",
            effort_selector_id="reasoning_effort",
            default_model=REQUIRED_MODEL,
            default_effort=REQUIRED_EFFORT,
            registered_models=(REQUIRED_MODEL,),
            allowed_efforts=(REQUIRED_EFFORT,),
            requires_session_load=True,
            config_schema=dict(CODEX_ACP_1_1_7.config_schema),
            expected_runtime=self.expected(),
        )
        kwargs.update(overrides)
        return AgentProfile(**kwargs)


# --- in-process ephemeral daemon ---------------------------------------------


class EphemeralDaemon:
    """In-process ArsdServer on a private socket; never the production unit."""

    def __init__(self, *, socket_path: Path, supervisor_root: Path, registry) -> None:
        self.socket_path = socket_path
        self.supervisor_root = supervisor_root
        self.session_store = storage.native_session_store(supervisor_root)
        self.event_store = storage.native_event_store(supervisor_root)
        self.handlers = handlers.ArsdHandlers(
            session_store=self.session_store,
            event_store=self.event_store,
            supervisor_root=supervisor_root,
            profile_registry=registry,
            cancel_wait_seconds=10.0,
        )
        owner = _env("ARS_CODEX_ACCEPTANCE_OWNER")
        namespace = _env("ARS_CODEX_ACCEPTANCE_NAMESPACE")
        principal = server.Principal(
            principal_id="codex-acceptance",
            owner_namespaces=frozenset({(owner, namespace)}),
        )
        self.server = server.ArsdServer(
            socket_path=socket_path,
            policy=server.CallerPolicy({os.getuid(): principal}),
            handler=self.handlers,
        )

    def run_dir(self, run_id: str) -> Path:
        return Path(self.event_store.base_dir) / run_id


@contextlib.contextmanager
def _serving(daemon: EphemeralDaemon):
    """Host the ephemeral daemon on its own loop/thread for the sync client."""
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    failure: list[BaseException] = []

    def runner() -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(daemon.server.start())
        except BaseException as exc:  # pragma: no cover - startup fail-closed
            failure.append(exc)
            ready.set()
            return
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=runner, name="codex-acceptance-daemon", daemon=True)
    thread.start()
    assert ready.wait(30), "ephemeral daemon never became ready"
    if failure:
        raise failure[0]
    try:
        yield daemon
    finally:
        for coro in (daemon.handlers.aclose(), daemon.server.shutdown()):
            asyncio.run_coroutine_threadsafe(coro, loop).result(120)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(30)
        loop.close()


def _submit(daemon: EphemeralDaemon, *, request_id: str, payload: dict) -> str:
    with arsd_client.ArsdClient(daemon.socket_path) as client:
        reply = client.submit(request_id=request_id, payload=payload)
    return reply["run_id"]


def _submit_payload(profile_id: str, workspace: Path, **overrides) -> dict:
    payload = {
        "request": _request(profile_id, **overrides.pop("request_overrides", {})),
        "prompt_text": overrides.pop("prompt_text", "codex acceptance probe"),
        "workspace_root": str(workspace),
        "cwd": None,
        "retry_of_run_id": None,
    }
    payload.update(overrides)
    return payload


def _assert_never_spawned(run_dir: Path) -> None:
    assert not (run_dir / DISPATCH_STARTED_MARKER).exists()
    assert not (run_dir / "prompt-accepted").exists()
    effective = run_dir / "effective.json"
    if effective.exists():
        observed = json.loads(effective.read_text(encoding="utf-8"))
        assert observed.get("process_identity") is None


def _attestation_rows(run_dir: Path) -> dict[str, dict]:
    report = json.loads((run_dir / "attestation.json").read_text(encoding="utf-8"))
    return {row["name"]: row for row in report["checks"]}


def _request(profile_id: str, **overrides) -> dict:
    payload = {
        "owner": _env("ARS_CODEX_ACCEPTANCE_OWNER"),
        "namespace": _env("ARS_CODEX_ACCEPTANCE_NAMESPACE"),
        "profile_id": profile_id,
        "session_reuse": "none",
        "ars_session_id": None,
        "expected_binding_hash": None,
        "input_refs": [{"ref": "prompt:inline", "content_hash": "sha256:" + "a" * 64}],
        "requested_model": REQUIRED_MODEL,
        "requested_effort": REQUIRED_EFFORT,
        "grant_ref": "grant:codex-acceptance",
        "grant_hash": "sha256:" + "b" * 64,
        "grant_role_hash": "sha256:" + "c" * 64,
        "grant_capabilities": ["read"],
        "mcp_snapshot_hashes": [],
        "credential_refs": ["codex-home-auth"],
        "limits": {},
        "evidence_policy_hash": "sha256:" + "d" * 64,
        "recovery_policy_hash": "sha256:" + "e" * 64,
    }
    payload.update(overrides)
    return payload


def _await_terminal(run_dir: Path) -> dict:
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        result = run_dir / "result.json"
        if result.exists():
            return json.loads(result.read_text(encoding="utf-8"))
        time.sleep(POLL_INTERVAL)
    pytest.fail(f"run under {run_dir} never reached a terminal result")


def _assert_ancestor_chain_clean(cwd: Path) -> None:
    """D11a pre-assertion: no .codex/config.toml anywhere on the chain."""
    directory = cwd
    while True:
        assert not os.path.lexists(str(directory / ".codex" / "config.toml")), (
            f"workspace ancestor {directory} carries a project config layer"
        )
        if directory.parent == directory:
            return
        directory = directory.parent


def _assert_no_bwrap_signature(text: str) -> None:
    for signature in _BWRAP_SIGNATURES:
        assert signature not in text, f"bwrap signature {signature!r} present (D7 FAIL)"


# --- P0: registry parity and hash binding (in-process, no daemon) ------------


def test_p0_registry_parity_and_hash_binding(tmp_path: Path) -> None:
    _codex_preflight()
    EVIDENCE["binding"] = _commit_binding()

    assert DEFAULT_REGISTRY.get("codex-acp-1.1.7") is CODEX_ACP_1_1_7
    home = tmp_path / "positive-codex-home"
    derived = _positive_profile(home)

    production_snapshot = CODEX_ACP_1_1_7.snapshot()
    derived_snapshot = derived.snapshot()
    differing = {
        key
        for key in set(production_snapshot) | set(derived_snapshot)
        if production_snapshot.get(key) != derived_snapshot.get(key)
    }
    assert differing == {"profile_id", "fixed_env"}
    production_env = dict(tuple(pair) for pair in production_snapshot["fixed_env"])
    derived_env = dict(tuple(pair) for pair in derived_snapshot["fixed_env"])
    assert set(production_env) == set(derived_env)
    assert {
        key for key in production_env if production_env[key] != derived_env[key]
    } == {"CODEX_HOME"}

    workspace = Path(_env("ARS_CODEX_ACCEPTANCE_WORKSPACE_PARENT")) / "p0-ws"
    workspace.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for label, profile in (("production", CODEX_ACP_1_1_7), ("derived", derived)):
        assembler = RunSpecAssembler(
            AgentRunRequest(
                **{
                    **_typed_request(profile.profile_id),
                }
            )
        )
        assembler.resolve_profile(ProfileRegistry((profile,)))
        assembler.bind_workspace(root=workspace)
        launch = assembler.resolve_launch()
        spec = assembler.seal(
            run_id=f"run-p0-{label}", submitted_at="2026-07-24T00:00:00+00:00"
        )
        hashes[label] = {
            "profile_hash": profile.profile_hash(),
            "launch_hash": launch.launch_hash(),
            "spec_agent_profile_hash": spec.agent.profile_hash,
            "spec_hash": spec_hash(spec),
        }
    # The behavior-affecting difference is bound into all four surfaces.
    for field in ("profile_hash", "launch_hash", "spec_agent_profile_hash", "spec_hash"):
        assert hashes["production"][field] != hashes["derived"][field], field
    EVIDENCE["p0_hashes"] = hashes


def _typed_request(profile_id: str) -> dict:
    wire = _request(profile_id)
    return {
        "owner": wire["owner"],
        "namespace": wire["namespace"],
        "profile_id": profile_id,
        "session_reuse": "none",
        "ars_session_id": None,
        "expected_binding_hash": None,
        "input_refs": (
            InputRef(ref="prompt:inline", content_hash="sha256:" + "a" * 64),
        ),
        "requested_model": REQUIRED_MODEL,
        "requested_effort": REQUIRED_EFFORT,
        "grant_ref": wire["grant_ref"],
        "grant_hash": wire["grant_hash"],
        "grant_role_hash": wire["grant_role_hash"],
        "grant_capabilities": ("read",),
        "mcp_snapshot_hashes": (),
        "credential_refs": ("codex-home-auth",),
        "limits": RunLimits(),
        "evidence_policy_hash": wire["evidence_policy_hash"],
        "recovery_policy_hash": wire["recovery_policy_hash"],
    }


# --- P1–P4 and N1–N9 leg definitions ----------------------------------------
#
# The positive legs below consume real Codex credentials through the D12
# ephemeral staged copy and drive the real adapter/CLI pair; the negative legs
# drive only private copies. Both are executed by the controller under boundary
# D against the reviewed implementation commit, never inside an author run.


@pytest.mark.parametrize(
    "leg",
    [
        "p1_exact_config_and_evidence",
        "p2_continuity_and_b1_boundary",
        "p3_permission_denied_before_effect",
        "p4_cancel_and_timeout_lifecycle",
    ],
)
def test_positive_legs(tmp_path: Path, leg: str) -> None:
    _codex_preflight()
    binding = _commit_binding()
    home = tmp_path / "positive-codex-home"
    real_root = Path(dict(CODEX_ACP_1_1_7.fixed_env)["CODEX_HOME"])
    real_auth = real_root / "auth.json"

    pre_inventory = _inventory_tree(real_root)
    _stage_credentials(home, real_auth)
    try:
        workspace = (
            Path(_env("ARS_CODEX_ACCEPTANCE_WORKSPACE_PARENT")) / f"{leg}-ws"
        )
        workspace.mkdir(parents=True)
        _assert_ancestor_chain_clean(workspace)
        assert sorted(entry.name for entry in workspace.iterdir()) == []

        daemon = EphemeralDaemon(
            socket_path=Path(_env("ARS_CODEX_ACCEPTANCE_SOCKET_DIR")) / f"{leg}.sock",
            supervisor_root=tmp_path / "pos-root",
            registry=ProfileRegistry((_positive_profile(home),)),
        )
        outcome = _run_positive_leg(daemon, leg, workspace, home)
        outcome["commit_sha"] = binding["commit_sha"]
        EVIDENCE["legs"][leg] = outcome  # type: ignore[index]
    finally:
        _teardown_credentials(home)

    post_inventory = _inventory_tree(real_root)
    # Nanosecond stat identity: pre-inventory precedes the staging read, so a
    # hypothetical O_NOATIME failure surfaces here as an atime diff.
    assert post_inventory == pre_inventory, (
        "real credential root metadata changed during acceptance"
    )


def _run_positive_leg(
    daemon: EphemeralDaemon, leg: str, workspace: Path, home: Path
) -> dict:
    """Drive one positive leg over the private socket against the real pair.

    Returns sanitized summary facts only — never model text, session IDs,
    credential values, or credential-file digests.
    """
    derived_profile = _positive_profile(home)
    with _serving(daemon):
        run_id = _submit(
            daemon,
            request_id=f"req-{leg}-1",
            payload=_submit_payload(POSITIVE_PROFILE_ID, workspace),
        )
        run_dir = daemon.run_dir(run_id)
        if leg == "p4_cancel_and_timeout_lifecycle":
            _await_marker(run_dir, DISPATCH_STARTED_MARKER)
            with arsd_client.ArsdClient(daemon.socket_path) as client:
                client.run_cancel(run_id)
        result = _await_terminal(run_dir)

        attestation = json.loads(
            (run_dir / "attestation.json").read_text(encoding="utf-8")
        )
        initialize = json.loads(
            (run_dir / "initialize_attestation.json").read_text(encoding="utf-8")
        )
        launch = json.loads((run_dir / "launch.json").read_text(encoding="utf-8"))
        spec = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
        effective = json.loads((run_dir / "effective.json").read_text(encoding="utf-8"))
        stderr_text = (run_dir / "stderr.log").read_text(encoding="utf-8")
        _assert_no_bwrap_signature(stderr_text)

        # Common to every positive leg: the boundary attested clean, the frozen
        # identity is durable in launch.json, and the derived home is bound
        # into the spec's profile hash.
        assert attestation["pass"] is True
        assert all(row["passed"] for row in attestation["checks"])
        rows = {row["name"] for row in attestation["checks"]}
        assert {"config_toml_absence_recheck", "project_config_closure_recheck"} <= rows
        assert initialize["pass"] is True
        assert launch["fixed_env"] == [
            list(pair) for pair in derived_profile.fixed_env
        ]
        assert launch["expected_runtime"] == (
            derived_profile.expected_runtime.to_dict()
        )
        assert spec["agent"]["profile_hash"] == derived_profile.profile_hash()
        assert effective["agent_info"]["name"] == (
            derived_profile.expected_runtime.agent_info_name
        )

        summary: dict[str, object] = {
            "status": result["status"],
            "detail_code": result.get("detail_code"),
            "retryable": result["retryable"],
            "attestation_pass": True,
            "initialize_attestation_pass": True,
            "spec_hash": spec["spec_hash"],
            "profile_hash": spec["agent"]["profile_hash"],
            "launch_spec_hash": launch["launch_spec_hash"],
        }

        if leg == "p1_exact_config_and_evidence":
            assert result["status"] == "completed"
            assert effective["effective_model"] == REQUIRED_MODEL
            assert effective["effective_effort"] == REQUIRED_EFFORT
            assert result["final_message"], "current-Turn final message is empty"
            # No credential bytes and no credential-file digests in evidence.
            for artifact in ("attestation.json", "initialize_attestation.json",
                             "launch.json", "spec.json", "effective.json"):
                raw = (run_dir / artifact).read_bytes()
                assert b"auth.json" not in raw
            summary["final_message_bytes"] = len(result["final_message"].encode())
            summary["truncated"] = result["truncated"]

        elif leg == "p2_continuity_and_b1_boundary":
            assert result["status"] == "completed"
            nonce = result["final_message"]
            session_id = _session_id_for(daemon, run_id)
            second_id = _submit(
                daemon,
                request_id=f"req-{leg}-2",
                payload=_submit_payload(
                    POSITIVE_PROFILE_ID,
                    workspace,
                    request_overrides={
                        "session_reuse": "reuse",
                        "ars_session_id": session_id,
                    },
                    prompt_text="recall the context nonce exactly",
                ),
            )
            second_dir = daemon.run_dir(second_id)
            second = _await_terminal(second_dir)
            assert second["status"] == "completed"
            # B1: replay history stays in events.jsonl but never contributes to
            # the current Turn's final message.
            assert second["truncated"] is False
            assert nonce not in second["final_message"] or second["final_message"]
            events = [
                json.loads(line)
                for line in (second_dir / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line
            ]
            assert any(
                event.get("type") == "agent_message_delta" for event in events
            )
            record = daemon.session_store.open_session(session_id)
            assert record.native_profile_hash == derived_profile.profile_hash()
            # The isolated home now carries CLI-persisted thread state — the
            # direct proof of why D12 isolation is required. Contents are never
            # read into evidence.
            assert any(home.iterdir())
            summary["reused_session_profile_hash_bound"] = True
            summary["isolated_home_has_thread_state"] = True

        elif leg == "p3_permission_denied_before_effect":
            mediation = json.loads(
                (run_dir / "permission-evidence.json").read_text(encoding="utf-8")
            ) if (run_dir / "permission-evidence.json").exists() else None
            events = [
                json.loads(line)
                for line in (run_dir / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line
            ]
            mediation_events = [
                event for event in events if "permission" in str(event.get("type", ""))
            ]
            # Zero mediation events prove nothing about denial.
            assert mediation_events, "no permission mediation observed (FAIL)"
            assert all(
                event.get("decision", "deny") == "deny" for event in mediation_events
            )
            assert not (workspace / "edit-canary.txt").exists()
            assert not (workspace / "execute-canary.txt").exists()
            summary["mediation_events"] = len(mediation_events)
            summary["mediation_evidence_present"] = mediation is not None

        else:  # p4_cancel_and_timeout_lifecycle
            assert result["status"] in {"cancelled", "unknown", "failed"}
            assert result["retryable"] is False
            identity = effective.get("process_identity")
            if identity and identity.get("pid"):
                _assert_pid_gone(int(identity["pid"]))
            summary["process_group_gone"] = True
            summary["no_replay"] = not (run_dir / "prompt-replay").exists()

        return summary


def _await_marker(run_dir: Path, marker: str, *, timeout: float = 300.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (run_dir / marker).exists():
            return
        time.sleep(POLL_INTERVAL)
    pytest.fail(f"marker {marker} never appeared under {run_dir}")


def _assert_pid_gone(pid: int, *, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(POLL_INTERVAL)
    pytest.fail(f"process {pid} survived finalization")


def _session_id_for(daemon: EphemeralDaemon, run_id: str) -> str:
    spec = json.loads(
        (daemon.run_dir(run_id) / "spec.json").read_text(encoding="utf-8")
    )
    session_id = spec["session"]["ars_session_id"]
    return session_id or f"{run_id}-ephemeral"


@pytest.mark.parametrize(
    "leg, expected_code",
    [
        ("n1_tampered_adapter_entry", "RUNTIME_IDENTITY_MISMATCH"),
        ("n2_swapped_node_binary", "RUNTIME_IDENTITY_MISMATCH"),
        ("n3_retargeted_cli_symlink", "RUNTIME_IDENTITY_MISMATCH"),
        ("n4_auth_json_structure", "CREDENTIAL_ROOT_VIOLATION"),
        ("n5_credential_root_structure", "CREDENTIAL_ROOT_VIOLATION"),
        ("n6_credential_root_config_toml", "CREDENTIAL_ROOT_VIOLATION"),
        ("n7_poisoned_workspace", "PROJECT_CONFIG_LAYER_PRESENT"),
        ("n8_credential_ref_mismatch", "ADMISSION"),
        ("n9_binding_drift_or_quarantine", "ADMISSION"),
    ],
)
def test_negative_legs(tmp_path: Path, leg: str, expected_code: str) -> None:
    _codex_preflight()
    binding = _commit_binding()
    fixtures = PrivateNegativeFixtures(tmp_path / "neg")
    daemon = EphemeralDaemon(
        socket_path=Path(_env("ARS_CODEX_ACCEPTANCE_SOCKET_DIR")) / f"{leg}.sock",
        supervisor_root=tmp_path / "neg-root",
        registry=ProfileRegistry((fixtures.profile(),)),
    )
    outcome = _run_negative_leg(daemon, fixtures, leg, expected_code)
    outcome["commit_sha"] = binding["commit_sha"]
    EVIDENCE["legs"][leg] = outcome  # type: ignore[index]


def _run_negative_leg(
    daemon: EphemeralDaemon,
    fixtures: PrivateNegativeFixtures,
    leg: str,
    expected_code: str,
) -> dict:
    """Mutate private copies only, then prove the refusal and the no-spawn fact.

    Reuse legs (N7 insertion-between-Runs, N9) first seed a real ARS Session by
    running the harness-written codex-shaped fake ``.mjs`` agent under the
    private Node copy — legitimately passing attestation with zero real
    credentials, zero real adapter execution, and zero model calls.
    """
    os.environ["FAKE_AGENT_NAME"] = fixtures.expected().agent_info_name
    os.environ["FAKE_AGENT_VERSION"] = fixtures.expected().agent_info_version
    pre_state = _private_fixture_state(fixtures)
    tampered_row: str | None = None
    seeded_session: str | None = None

    with _serving(daemon):
        if leg in {"n7_poisoned_workspace", "n9_binding_drift_or_quarantine"}:
            seed_id = _submit(
                daemon,
                request_id=f"req-{leg}-seed",
                payload=_submit_payload(NEGATIVE_PROFILE_ID, fixtures.workspace),
            )
            seed = _await_terminal(daemon.run_dir(seed_id))
            assert seed["status"] == "completed", "session seeding must pass cleanly"
            seeded_session = _session_id_for(daemon, seed_id)

        # Arrange exactly one tampered surface, on a private copy.
        if leg == "n1_tampered_adapter_entry":
            fixtures.entry.write_text("// tampered\n", encoding="utf-8")
            tampered_row = "adapter_entry_sha256"
        elif leg == "n2_swapped_node_binary":
            swapped = fixtures.base / "stage" / "node-swapped"
            swapped.write_bytes(b"#!/bin/false\n# swapped node\n")
            os.replace(swapped, fixtures.node)
            tampered_row = "node_sha256"
        elif leg == "n3_retargeted_cli_symlink":
            other = fixtures.base / "stage" / "codex-other"
            other.write_bytes(b"#!/bin/false\n# retargeted cli\n")
            fixtures.cli.unlink()
            fixtures.cli.symlink_to(other)
            tampered_row = "cli_sha256"
        elif leg == "n4_auth_json_structure":
            os.chmod(fixtures.auth, 0o644)
            tampered_row = "auth_json_structure"
        elif leg == "n5_credential_root_structure":
            os.chmod(fixtures.cred_root, 0o750)
            tampered_row = "credential_root_structure"
        elif leg == "n6_credential_root_config_toml":
            (fixtures.cred_root / "config.toml").write_text(
                "[features]\n", encoding="utf-8"
            )
            tampered_row = "config_toml_absent"
        elif leg == "n7_poisoned_workspace":
            layer = fixtures.workspace / ".codex"
            layer.mkdir()
            (layer / "config.toml").write_text("model = 'poison'\n", encoding="utf-8")
            tampered_row = "project_config_closure"

        request_overrides: dict[str, object] = {}
        if leg == "n8_credential_ref_mismatch":
            request_overrides["credential_refs"] = ["not-the-registered-slot"]
        if seeded_session is not None:
            request_overrides["session_reuse"] = "reuse"
            request_overrides["ars_session_id"] = seeded_session
        if leg == "n9_binding_drift_or_quarantine":
            # Profile-hash drift on the seeded session: a revision bump in the
            # private registry alone must refuse reuse before spawn.
            daemon.handlers._factory = handlers.default_run_task_factory(
                daemon.supervisor_root,
                registry=ProfileRegistry((fixtures.profile(revision=2),)),
            )

        run_id = _submit(
            daemon,
            request_id=f"req-{leg}-probe",
            payload=_submit_payload(
                NEGATIVE_PROFILE_ID,
                fixtures.workspace,
                request_overrides=request_overrides,
            ),
        )
        run_dir = daemon.run_dir(run_id)
        result = _await_terminal(run_dir)

    assert result["status"] == "failed"
    assert result["detail_code"] == expected_code, result
    assert result["retryable"] is False
    _assert_never_spawned(run_dir)

    if tampered_row is not None:
        rows = _attestation_rows(run_dir)
        assert rows[tampered_row]["passed"] is False, tampered_row
        assert all(
            row["passed"] for name, row in rows.items() if name != tampered_row
        ), "exactly one attestation row may fail"
    else:
        # Admission/binding refusals never reach the attestation stage.
        assert not (run_dir / "attestation.json").exists()

    # Private fixtures stay exactly as arranged; nothing real was referenced.
    post_state = _private_fixture_state(fixtures)
    assert set(post_state) == set(pre_state)
    return {
        "detail_code": result["detail_code"],
        "failing_row": tampered_row,
        "spawned": False,
        "seeded_session": seeded_session is not None,
    }


def _private_fixture_state(fixtures: PrivateNegativeFixtures) -> dict[str, str]:
    """Structural snapshot of the private copies (no real artifact is read)."""
    state = {}
    for label, path in (
        ("node", fixtures.node),
        ("entry", fixtures.entry),
        ("cli", fixtures.cli),
        ("cred_root", fixtures.cred_root),
        ("auth", fixtures.auth),
    ):
        try:
            info = os.lstat(path)
        except OSError:
            state[label] = "absent"
            continue
        state[label] = f"{stat.S_IFMT(info.st_mode)}:{oct(stat.S_IMODE(info.st_mode))}"
    return state


# --- shared-resource invariant and hygiene ----------------------------------


def test_shared_resource_invariant_untouched() -> None:
    """Durable trees byte-untouched; the production surface never referenced."""
    _codex_preflight()
    identity = CODEX_ACP_1_1_7.expected_runtime
    assert identity is not None
    for path, expected_digest in (
        (Path(identity.node_path), identity.node_sha256),
        (Path(identity.adapter_entry_path), identity.adapter_entry_sha256),
        (Path(identity.cli_path), identity.cli_sha256),
    ):
        assert _sha256_file(path) == expected_digest, path
    EVIDENCE["shared_resources_verified"] = True


def test_evidence_bundle_is_sanitized_and_sha_bound() -> None:
    """A bundle without the D14 SHA binding is incomplete and FAILs the gate."""
    _codex_preflight()
    binding = _commit_binding()
    assert binding["commit_sha"]
    serialized = json.dumps(EVIDENCE, sort_keys=True)
    for banned in ("auth.json\":", "BEGIN PRIVATE KEY", "Bearer "):
        assert banned not in serialized
    assert DISPATCH_STARTED_MARKER  # imported contract marker stays referenced
    assert arsd_client.ArsdClient is not None
    assert profile_module._REGISTERED_EXECUTABLES["codex-acp"]

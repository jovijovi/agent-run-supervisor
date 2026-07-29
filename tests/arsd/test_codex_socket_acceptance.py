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
# The Binding inputs are *required*, not optional: after the R13 split the
# downstream CLI path/version/digest and the credential-root value are operator
# deployment facts, so a positive leg cannot be assembled from source constants
# at all. An operator who has not prepared an immutable, non-service-writable
# artifact root and authored a generation for it cannot opt in — which is the
# intended fail-closed consequence, not a harness limitation to work around.
_REQUIRED_ENV = (
    "ARS_CODEX_ACCEPTANCE_SOCKET_DIR",
    "ARS_CODEX_ACCEPTANCE_SUPERVISOR_ROOT",
    "ARS_CODEX_ACCEPTANCE_WORKSPACE_PARENT",
    "ARS_CODEX_ACCEPTANCE_OWNER",
    "ARS_CODEX_ACCEPTANCE_NAMESPACE",
    "ARS_CODEX_ACCEPTANCE_CALLER_MAPPING",
    "ARS_CODEX_ACCEPTANCE_COMMIT_SHA",
    "ARS_CODEX_ACCEPTANCE_BINDING_ROOT",
    "ARS_CODEX_ACCEPTANCE_TRUSTED_UID",
    "ARS_CODEX_ACCEPTANCE_REAL_CREDENTIAL_ROOT",
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
from agent_run_supervisor.native_acp import runtime_binding as rb  # noqa: E402
from agent_run_supervisor.native_acp.attestation import (  # noqa: E402
    ArtifactClosure,
    SealedRuntimeIdentity,
)
from agent_run_supervisor.native_acp.profile import (  # noqa: E402
    CODEX_ACP_1_1_7,
    DEFAULT_REGISTRY,
    SLOT_KIND_CONFIG_ROOT,
    SLOT_KIND_PACKAGE_TREE,
    AdapterContract,
    AgentProfile,
    BindingSlot,
    ProfileRegistry,
    VersionProbeRule,
    WrappedRuntimeArtifacts,
)
from agent_run_supervisor.native_acp.run_task import (  # noqa: E402
    DISPATCH_STARTED_MARKER,
    PROMPT_ACCEPTED_MARKER,
)
from agent_run_supervisor.native_acp.spec import (  # noqa: E402
    AgentRunRequest,
    InputRef,
    RunLimits,
    RunSpecAssembler,
    spec_hash,
)

REQUIRED_MODEL = "gpt-5.6-sol"
REQUIRED_EFFORT = "max"
# The acceptance run uses the registered profile unchanged: re-pointing the
# credential root is now a Binding generation, not a derived source row.
POSITIVE_PROFILE_ID = CODEX_ACP_1_1_7_PROFILE_ID = "codex-acp-1.1.7"
NEGATIVE_PROFILE_ID = "codex-acp-e2e-neg"
NEGATIVE_EXECUTABLE_KEY = "codex-acp-e2e"
RUN_TIMEOUT_SECONDS = 900
POLL_INTERVAL = 0.5

# The staged ephemeral credential copy is excluded from every isolated-home
# state snapshot: it is harness-created, so counting it would make "new CLI
# thread/rollout state appeared" true before the agent ever ran.
STAGED_CREDENTIAL_NAME = "auth.json"

# P4 drives two distinct positive sublegs over the same UDS harness. Both
# outcomes are the B2-fixed terminal table rows for a dispatched Turn with no
# trustworthy ACP terminal (`finalize_run_state`), not harness opinion:
# escalated kill after dispatch ⇒ unknown/quarantined/retryable=false, with the
# trigger distinguished only by `detail_code`.
P4_CANCEL_LEG = "p4_cancel_after_dispatch"
P4_TIMEOUT_LEG = "p4_timeout_after_dispatch"
P4_EXPECTED_OUTCOMES: dict[str, dict[str, object]] = {
    P4_CANCEL_LEG: {
        "status": "unknown",
        "detail_code": "SUPERVISOR_CANCELLED",
        "retryable": False,
        "session_state": "quarantined",
    },
    P4_TIMEOUT_LEG: {
        "status": "unknown",
        "detail_code": "TURN_TIMEOUT",
        "retryable": False,
        "session_state": "quarantined",
    },
}
# Short enough that a real model Turn cannot finish inside it, and applied only
# to the post-dispatch prompt await (`turn_timeout_seconds` bounds
# `prompt_once`), so the timeout provably cannot fire before spawn/admission.
P4_TURN_TIMEOUT_SECONDS = 5.0

POSITIVE_LEGS = (
    "p1_exact_config_and_evidence",
    "p2_continuity_and_b1_boundary",
    "p3_permission_denied_before_effect",
    P4_CANCEL_LEG,
    P4_TIMEOUT_LEG,
)
# Legs whose Turn result is checked against a token chosen before the Run.
NONCE_LEGS = ("p1_exact_config_and_evidence", "p2_continuity_and_b1_boundary")


@dataclasses.dataclass(frozen=True)
class NegativeCase:
    """One boundary-D refusal leg: exactly one arranged tamper, one outcome."""

    case_id: str
    family: str
    detail_code: str
    # The attestation row that must FAIL (all other recorded rows PASS), or
    # None for cases that must be refused before the attestation stage.
    failing_row: str | None
    stage: str
    # Session-seeded legs need a real prior Run on a reusable Session.
    seeded: bool = False


# Every R8 N1–N9 variant, declared explicitly. `test_negative_legs` drives this
# tuple, and the hermetic contract suite fails if a required member is missing
# or duplicated — a family can never be represented by one specimen again.
NEGATIVE_CASES: tuple[NegativeCase, ...] = (
    NegativeCase(
        "n1_tampered_adapter_entry",
        "n1",
        "RUNTIME_IDENTITY_MISMATCH",
        "adapter_entry_sha256",
        "attestation",
    ),
    NegativeCase(
        "n2_swapped_node_binary",
        "n2",
        "RUNTIME_IDENTITY_MISMATCH",
        "node_sha256",
        "attestation",
    ),
    # The downstream CLI is a Binding artifact now, so a retarget is caught at
    # the single per-Run Binding read — strictly earlier than the attestation
    # row it used to fail, and before any Run artifact beyond the terminal.
    NegativeCase(
        "n3_retargeted_cli_symlink",
        "n3",
        "REGISTRATION_FAILED",
        None,
        "binding",
    ),
    NegativeCase(
        "n4_auth_json_symlink",
        "n4",
        "CREDENTIAL_ROOT_VIOLATION",
        "auth_json_structure",
        "attestation",
    ),
    NegativeCase(
        "n4_auth_json_mode_0644",
        "n4",
        "CREDENTIAL_ROOT_VIOLATION",
        "auth_json_structure",
        "attestation",
    ),
    NegativeCase(
        "n4_auth_json_removed",
        "n4",
        "CREDENTIAL_ROOT_VIOLATION",
        "auth_json_structure",
        "attestation",
    ),
    NegativeCase(
        "n5_credential_root_mode_0750",
        "n5",
        "CREDENTIAL_ROOT_VIOLATION",
        "credential_root_structure",
        "attestation",
    ),
    # A symlinked config root fails the Binding read's own directory shape
    # check before the credential-root structure row is reached.
    NegativeCase(
        "n5_credential_root_symlink",
        "n5",
        "REGISTRATION_FAILED",
        None,
        "binding",
    ),
    NegativeCase(
        "n6_credential_root_config_toml",
        "n6",
        "CREDENTIAL_ROOT_VIOLATION",
        "config_toml_absent",
        "attestation",
    ),
    NegativeCase(
        "n7_project_config_at_cwd",
        "n7",
        "PROJECT_CONFIG_LAYER_PRESENT",
        "project_config_closure",
        "attestation",
    ),
    NegativeCase(
        "n7_project_config_at_workspace_root",
        "n7",
        "PROJECT_CONFIG_LAYER_PRESENT",
        "project_config_closure",
        "attestation",
    ),
    NegativeCase(
        "n7_project_config_above_workspace_root",
        "n7",
        "PROJECT_CONFIG_LAYER_PRESENT",
        "project_config_closure",
        "attestation",
    ),
    NegativeCase(
        "n7_project_config_inserted_between_runs",
        "n7",
        "PROJECT_CONFIG_LAYER_PRESENT",
        "project_config_closure",
        "attestation",
        seeded=True,
    ),
    NegativeCase(
        "n8_credential_refs_missing", "n8", "ADMISSION", None, "admission"
    ),
    NegativeCase("n8_credential_refs_wrong", "n8", "ADMISSION", None, "admission"),
    NegativeCase("n8_credential_refs_extra", "n8", "ADMISSION", None, "admission"),
    # Session-binding refusals surface through the RunTask top-level guard, not
    # through admission: `_bind_session` raises SessionBindingError /
    # SessionQuarantinedError before any attestation call.
    NegativeCase(
        "n9_seeded_session_profile_hash_drift",
        "n9",
        "RUN_EXCEPTION",
        None,
        "session",
        seeded=True,
    ),
    NegativeCase(
        "n9_quarantined_session_reuse",
        "n9",
        "RUN_EXCEPTION",
        None,
        "session",
        seeded=True,
    ),
)

# The R8 §2.3 matrix, restated as data so omission is a test failure rather
# than a reading of prose.
REQUIRED_NEGATIVE_VARIANTS: dict[str, tuple[str, ...]] = {
    "n1": ("n1_tampered_adapter_entry",),
    "n2": ("n2_swapped_node_binary",),
    "n3": ("n3_retargeted_cli_symlink",),
    "n4": (
        "n4_auth_json_symlink",
        "n4_auth_json_mode_0644",
        "n4_auth_json_removed",
    ),
    "n5": ("n5_credential_root_mode_0750", "n5_credential_root_symlink"),
    "n6": ("n6_credential_root_config_toml",),
    "n7": (
        "n7_project_config_at_cwd",
        "n7_project_config_at_workspace_root",
        "n7_project_config_above_workspace_root",
        "n7_project_config_inserted_between_runs",
    ),
    "n8": (
        "n8_credential_refs_missing",
        "n8_credential_refs_wrong",
        "n8_credential_refs_extra",
    ),
    "n9": (
        "n9_seeded_session_profile_hash_drift",
        "n9_quarantined_session_reuse",
    ),
}

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


def _teardown_credentials(home: Path) -> tuple[str, ...]:
    """Best-effort logical cleanup; the retained proof is absence, not erasure.

    Returns the residue findings (empty tuple = clean). Nothing here raises on
    an ordinary cleanup error: this runs on *every* failure path, including a
    half-written staged copy, and must always reach the unlink and the tree
    removal. Findings are errno/label strings only — never bytes or digests.
    """
    staged = home / STAGED_CREDENTIAL_NAME
    problems: list[str] = []
    try:
        if staged.exists():
            try:
                size = staged.stat().st_size
                fd = os.open(str(staged), os.O_WRONLY | os.O_CLOEXEC)
                try:
                    os.write(fd, b"\x00" * size)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError as exc:
                problems.append(f"zero_overwrite_failed:{exc.errno}")
            try:
                staged.unlink()
            except OSError as exc:
                problems.append(f"staged_unlink_failed:{exc.errno}")
    except OSError as exc:  # pragma: no cover - stat/exists on a broken tree
        problems.append(f"staged_probe_failed:{exc.errno}")
    finally:
        shutil.rmtree(home, ignore_errors=True)
    if staged.exists():
        problems.append("staged_credential_copy_survived")
    if home.exists():
        problems.append("ephemeral_home_survived")
    return tuple(problems)


def _inventory_drift(
    pre: dict[str, dict[str, int]], post: dict[str, dict[str, int]]
) -> tuple[str, ...]:
    """Which nanosecond stat facts moved, named by *field* only.

    Entry names inside the real credential root are never returned: a drifted
    field name plus a count is enough to fail and investigate, and keeps the
    root's contents out of every message and log.
    """
    if set(pre) != set(post):
        return ("entry_set", f"entries:{len(set(pre) ^ set(post))}")
    fields: set[str] = set()
    changed = 0
    for key, facts in pre.items():
        differing = {
            field for field, value in facts.items() if post[key].get(field) != value
        }
        if differing:
            changed += 1
            fields |= differing
    if not fields:
        return ()
    return tuple(sorted(fields)) + (f"entries:{changed}",)


class CredentialIsolationError(AssertionError):
    """Staging cleanup or the real-root invariant failed.

    Distinct from a leg's own failure on purpose: this is raised from the
    guard's ``finally``, so it appears *chained to* — never instead of — the
    original error, and only when cleanup or the inventory revealed the
    stronger invariant failure.
    """


@contextlib.contextmanager
def _credential_isolation(home: Path, real_auth: Path, real_root: Path):
    """Stage → run → always clean, always re-inventory.

    The staging call sits **inside** the guarded region. A write, fsync, close,
    or any post-write failure therefore still zeroes/unlinks the staged copy,
    removes the ephemeral home, and re-runs the D12 nanosecond pre/post
    identity check on the real root — the check must run on every staging and
    leg failure path, not only after a clean leg.
    """
    pre_inventory = _inventory_tree(real_root)
    try:
        _stage_credentials(home, real_auth)
        yield pre_inventory
    finally:
        residue = _teardown_credentials(home)
        drift = _inventory_drift(pre_inventory, _inventory_tree(real_root))
        if residue or drift:
            # Raised from `finally`: any in-flight leg exception is preserved
            # as this error's context, never swallowed.
            raise CredentialIsolationError(
                "credential isolation invariant failed "
                f"(cleanup residue: {list(residue)}; real-root drift: {list(drift)})"
            )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# The negative legs' private artifacts are owned by the run's own UID, so the
# declared service UID must be one that owns nothing at all.
_FAKE_SERVICE_UID = 4_000_000_001


def _write_canonical(path: Path, payload: dict) -> Path:
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    os.chmod(path, 0o644)
    return path


# --- profiles ----------------------------------------------------------------


def _acceptance_ownership() -> rb.TrustedOwnership:
    """The operator's declared artifact-trust policy for this acceptance run."""
    return rb.TrustedOwnership(
        trusted_uids=frozenset({int(_env("ARS_CODEX_ACCEPTANCE_TRUSTED_UID"))}),
        service_uid=os.geteuid(),
    )


def _positive_binding() -> rb.AdmittedRuntimeBinding:
    """The operator-prepared generation the positive legs run against.

    The credential isolation that used to come from a derived profile with an
    ephemeral ``CODEX_HOME`` now comes from the Binding itself: the operator
    promotes an acceptance generation whose ``codex_home`` slot is *not* the
    production credential root, and the harness proves that before it runs.
    The registered profile is used unchanged — re-pointing a deployment fact no
    longer needs a source change, which is the whole point of R13.
    """
    ownership = _acceptance_ownership()
    reader = rb.BindingReader(
        Path(_env("ARS_CODEX_ACCEPTANCE_BINDING_ROOT")), ownership=ownership
    )
    resolved = reader.resolve_active(CODEX_ACP_1_1_7)
    staged_home = Path(resolved.slot("codex_home").descriptor["path"]).resolve()
    real_root = Path(_env("ARS_CODEX_ACCEPTANCE_REAL_CREDENTIAL_ROOT")).resolve()
    if staged_home == real_root:
        raise CredentialIsolationError(
            "the acceptance generation must not bind the production credential root"
        )
    return rb.AdmittedRuntimeBinding(resolved=resolved, ownership=ownership)


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
        real_node = Path(
            DEFAULT_REGISTRY.get(
                "codex-acp-1.1.7"
            ).contract.wrapped_runtime.interpreter_path
        )
        self.node = stage / "node"
        shutil.copy2(real_node, self.node)
        os.chmod(self.node, 0o555)
        # The adapter is a package closure: the entry lives inside its install
        # root, beside the hoisted dependency Node reaches by walking up.
        self.adapter_root = stage / "adapter-pkg"
        self.entry = (
            self.adapter_root / "node_modules" / "@scope" / "adapter" / "dist"
            / "codex-fake-agent.mjs"
        )
        self.entry.parent.mkdir(parents=True)
        self.entry.write_text(CODEX_FAKE_AGENT_MJS, encoding="utf-8")
        self.adapter_sibling = self.adapter_root / "node_modules" / "dep" / "index.js"
        self.adapter_sibling.parent.mkdir(parents=True)
        self.adapter_sibling.write_bytes(b"// private hoisted dependency\n")
        # The downstream CLI is a package closure, and a Binding names an
        # immutable regular file: no symlink stands in for the launcher.
        self.package_root = stage / "codex-pkg"
        (self.package_root / "lib").mkdir(parents=True)
        (self.package_root / "lib" / "sibling.js").write_bytes(b"// private sibling\n")
        self.cli = self.package_root / "codex"
        self.cli.write_bytes(b"#!/bin/false\n# private placeholder cli\n")
        os.chmod(self.cli, 0o555)
        self.cli_target = self.cli
        self.cli_interpreter = stage / "cli-node"
        self.cli_interpreter.write_bytes(b"#!/bin/false\n# private cli interpreter\n")
        os.chmod(self.cli_interpreter, 0o555)
        self.cred_root = base / "synthetic-codex-home"
        self.cred_root.mkdir(mode=0o700)
        os.chmod(self.cred_root, 0o700)
        self.auth = self.cred_root / "auth.json"
        self.auth.write_bytes(SYNTHETIC_AUTH_BYTES)
        os.chmod(self.auth, 0o600)
        self.workspace = base / "neg-workspace"
        self.workspace.mkdir()
        # A cwd strictly below the workspace root, so the N7 variants can put a
        # project-config layer at the cwd, at the root, and above the root and
        # still be three distinguishable positions on one ancestor chain.
        self.nested_cwd = self.workspace / "nested"
        self.nested_cwd.mkdir()

    def closure(self) -> ArtifactClosure:
        return ArtifactClosure(
            kind="package_tree",
            path=str(self.cli),
            sha256=_sha256_file(self.cli),
            version="0.0.0-private",
            package_root=str(self.package_root),
            tree_sha256=rb.package_tree_digest(self.package_root),
            interpreter_path=str(self.cli_interpreter),
            interpreter_sha256=_sha256_file(self.cli_interpreter),
        )

    def expected(self) -> SealedRuntimeIdentity:
        return SealedRuntimeIdentity(
            launch_kind="wrapped_acp",
            agent_info_name="@example/codex-e2e-fake",
            agent_info_version="1.1.7",
            protocol_version="1",
            cli=self.closure(),
            cli_path_env="CODEX_PATH",
            node_path=str(self.node),
            node_sha256=_sha256_file(self.node),
            adapter_entry_path=str(self.entry),
            adapter_entry_sha256=_sha256_file(self.entry),
            adapter_package_root=str(self.adapter_root),
            adapter_tree_sha256=rb.package_tree_digest(self.adapter_root),
            interpreter_argv_prefix=("--no-global-search-paths",),
            credential_root_env="CODEX_HOME",
            credential_root_path=str(self.cred_root),
            project_config_relpath=".codex/config.toml",
        )

    def contract(self, **overrides) -> AdapterContract:
        kwargs = dict(
            launch_kind="wrapped_acp",
            acp_agent_name="@example/codex-e2e-fake",
            acp_protocol_version="1",
            acp_agent_version="1.1.7",
            version_probe=VersionProbeRule(argv_suffix=("--version",)),
            binding_slots=(
                BindingSlot(
                    name="downstream_cli",
                    kind=SLOT_KIND_PACKAGE_TREE,
                    env_key="CODEX_PATH",
                ),
                BindingSlot(
                    name="codex_home",
                    kind=SLOT_KIND_CONFIG_ROOT,
                    env_key="CODEX_HOME",
                ),
            ),
            wrapped_runtime=WrappedRuntimeArtifacts(
                interpreter_path=str(self.node),
                interpreter_sha256=_sha256_file(self.node),
                adapter_entry_path=str(self.entry),
                adapter_entry_sha256=_sha256_file(self.entry),
                adapter_package_root=str(self.adapter_root),
                adapter_tree_sha256=rb.package_tree_digest(self.adapter_root),
                interpreter_argv_prefix=("--no-global-search-paths",),
            ),
            cli_slot="downstream_cli",
            credential_root_slot="codex_home",
            project_config_relpath=".codex/config.toml",
        )
        kwargs.update(overrides)
        return AdapterContract(**kwargs)

    def binding_slots(self) -> dict:
        closure = self.closure()
        return {
            "downstream_cli": {
                "kind": SLOT_KIND_PACKAGE_TREE,
                "package_root": closure.package_root,
                "tree_sha256": closure.tree_sha256,
                "launcher_path": closure.path,
                "launcher_sha256": closure.sha256,
                "interpreter_path": closure.interpreter_path,
                "interpreter_sha256": closure.interpreter_sha256,
                "version": closure.version,
            },
            "codex_home": {
                "kind": SLOT_KIND_CONFIG_ROOT,
                "path": str(self.cred_root),
            },
        }

    def write_binding_root(self, profile: AgentProfile) -> Path:
        """A private Binding root for the negative legs — never a real one."""
        root = self.base / "binding-root"
        generation = root / rb.GENERATIONS_DIRNAME / "gen-neg"
        generation.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": rb.BINDING_SCHEMA_VERSION,
            "generation_id": "gen-neg",
            "contract_identity": {
                "profile_id": profile.profile_id,
                "profile_revision": profile.revision,
                "adapter_contract_hash": profile.adapter_contract_hash(),
            },
            "slots": self.binding_slots(),
            "session_compatibility_epoch": 1,
            "provenance": {
                "created_at": "2026-07-26T00:00:00+00:00",
                "accepted_by": "acceptance-harness",
                "accepted_at": "2026-07-26T00:00:00+00:00",
                "acceptance_receipt": {"ref": "receipt:private", "sha256": "0" * 64},
            },
        }
        manifest_path = generation / rb.MANIFEST_FILENAME
        _write_canonical(manifest_path, manifest)
        _write_canonical(
            root / rb.ACTIVE_FILENAME,
            {
                "schema_version": rb.BINDING_SCHEMA_VERSION,
                "generation_id": "gen-neg",
                "manifest_sha256": _sha256_file(manifest_path),
            },
        )
        for directory in (root, root / rb.GENERATIONS_DIRNAME, generation):
            os.chmod(directory, 0o755)
        return root

    def binding_ownership(self) -> rb.TrustedOwnership:
        """Trust this run's own UID; a fake service UID owns nothing here."""
        return rb.TrustedOwnership(
            trusted_uids=frozenset({os.getuid()}), service_uid=_FAKE_SERVICE_UID
        )

    def profile(self, **overrides) -> AgentProfile:
        contract_overrides = overrides.pop("contract_overrides", {})
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
            contract=self.contract(**contract_overrides),
        )
        kwargs.update(overrides)
        return AgentProfile(**kwargs)


# --- in-process ephemeral daemon ---------------------------------------------


class EphemeralDaemon:
    """In-process ArsdServer on a private socket; never the production unit."""

    def __init__(
        self,
        *,
        socket_path: Path,
        supervisor_root: Path,
        registry,
        binding_root: Path,
        binding_ownership: rb.TrustedOwnership,
    ) -> None:
        self.socket_path = socket_path
        self.supervisor_root = supervisor_root
        self.session_store = storage.native_session_store(supervisor_root)
        self.event_store = storage.native_event_store(supervisor_root)
        self.handlers = handlers.ArsdHandlers(
            session_store=self.session_store,
            event_store=self.event_store,
            supervisor_root=supervisor_root,
            profile_registry=registry,
            binding_root=binding_root,
            binding_ownership=binding_ownership,
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


# --- P2 continuity: nonce, current-turn separation, thread-state delta -------


def _context_nonce(leg: str, commit_sha: str) -> str:
    """A deterministic, non-secret continuity token.

    Derived only from public inputs (the leg name and the reviewed commit SHA)
    so a rerun of the same gate asks for the same token, and shaped so a model
    can echo it back byte-exactly.
    """
    seed = f"ars-codex-acceptance:{leg}:{commit_sha}".encode()
    return "ARS-CONTINUITY-" + hashlib.sha256(seed).hexdigest()[:12].upper()


def _nonce_prompt(nonce: str) -> str:
    return (
        "Remember this exact context token for the rest of this session: "
        f"{nonce}. Reply with the token alone — no punctuation, no explanation."
    )


RECALL_PROMPT = (
    "Reply with the exact context token I gave you earlier in this session, "
    "and nothing else."
)


def _home_state(home: Path) -> frozenset[str]:
    """Relative entry names under the isolated home, minus the staged copy.

    Names are compared in-process only; the retained evidence records their
    count, never the names themselves and never any content.
    """
    entries: set[str] = set()
    for path in home.rglob("*"):
        relative = path.relative_to(home).as_posix()
        if relative == STAGED_CREDENTIAL_NAME:
            continue
        entries.add(relative)
    return frozenset(entries)


def _assert_new_thread_state(
    before: frozenset[str], after: frozenset[str]
) -> int:
    """The isolated home must gain CLI-persisted thread/rollout state.

    An `any(iterdir())` form cannot prove this: the staged ``auth.json`` alone
    already satisfies it before the agent has ever run.
    """
    delta = after - before
    if not delta:
        raise AssertionError(
            "no new CLI thread/rollout state appeared in the isolated home"
        )
    return len(delta)


def _assert_exact_nonce_recall(final_message: str, nonce: str) -> None:
    """Exact recall: the current Turn's message is the nonce, once, alone.

    Never a substring test and never an `or <truthy>` disjunction — both pass
    for any non-empty response and would let a Run without continuity through.
    """
    if not nonce:
        raise AssertionError("continuity nonce must be non-empty")
    if final_message.count(nonce) != 1:
        raise AssertionError(
            "continuity nonce must appear exactly once in the current-Turn "
            "final message (0 = no recall, >1 = replay concatenation)"
        )
    if final_message.strip() != nonce:
        raise AssertionError(
            "current-Turn final message is not exactly the continuity nonce"
        )


def _prompt_boundary_index(events: list[dict]) -> int:
    for index, event in enumerate(events):
        if event.get("type") == "session_prompt_sent":
            return index
    raise AssertionError("no session_prompt_sent event: the Turn never dispatched")


def _message_lengths(events: list[dict], *, after_prompt: bool) -> int:
    """Summed ``text_length`` of agent_message deltas on one side of the prompt.

    Normalized events carry lengths, never text, so this stays a structural
    measurement of the ACP message path.
    """
    boundary = _prompt_boundary_index(events)
    window = events[boundary + 1 :] if after_prompt else events[:boundary]
    return sum(
        int(event.get("text_length", 0))
        for event in window
        if event.get("type") == "agent_message_delta"
    )


def _assert_current_turn_message_path(
    events: list[dict], final_message: str
) -> dict[str, int]:
    """The final message is exactly the current Turn's ACP message stream.

    Replay delivered by ``session/load`` stays visible as normalized events but
    contributes zero bytes here — the B1 boundary, measured rather than
    assumed.
    """
    current = _message_lengths(events, after_prompt=True)
    if current == 0:
        raise AssertionError("no current-Turn agent_message_delta events")
    if current != len(final_message):
        raise AssertionError(
            "final message length does not equal the current-Turn ACP message "
            "stream (replay or history leaked into the Turn result)"
        )
    return {
        "current_turn_message_length": current,
        "replayed_message_length": _message_lengths(events, after_prompt=False),
    }


# --- P4 lifecycle: two sublegs against the B2-fixed terminal table -----------


def _assert_p4_outcome(leg: str, result: dict) -> None:
    expected = P4_EXPECTED_OUTCOMES[leg]
    for field in ("status", "detail_code", "retryable"):
        observed = result.get(field)
        if observed != expected[field]:
            raise AssertionError(
                f"P4 {leg}: {field} is {observed!r}, not the B2-fixed "
                f"{expected[field]!r}"
            )


def _assert_no_prompt_replay(events: list[dict], run_dir: Path) -> None:
    """The prompt was dispatched exactly once and never re-sent."""
    sent = [event for event in events if event.get("type") == "session_prompt_sent"]
    if len(sent) != 1:
        raise AssertionError(
            f"prompt dispatched {len(sent)} times; exactly one is allowed"
        )
    marker = json.loads(
        (run_dir / DISPATCH_STARTED_MARKER).read_text(encoding="utf-8")
    )
    if marker["marker"] != DISPATCH_STARTED_MARKER:
        raise AssertionError("dispatch marker does not name its own boundary")


def _assert_process_group_gone(identity: dict | None) -> int | None:
    """The child leads its own session, so its pgid equals its pid."""
    if not identity or not identity.get("pid"):
        return None
    pid = int(identity["pid"])
    _assert_pid_gone(pid)
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return pid
    except PermissionError:  # pragma: no cover - foreign group reuse
        pytest.fail("process group id was reused by a foreign process")
    pytest.fail(f"process group {pid} survived finalization")
    return None


# --- P0: registry parity and hash binding (in-process, no daemon) ------------


def test_p0_registry_parity_and_hash_binding(tmp_path: Path) -> None:
    _codex_preflight()
    EVIDENCE["binding"] = _commit_binding()

    assert DEFAULT_REGISTRY.get("codex-acp-1.1.7") is CODEX_ACP_1_1_7
    # The acceptance run uses the *registered* profile unchanged: after the R13
    # split, re-pointing the credential root is a Binding generation, not a
    # derived source row. The parity claim is therefore about the generation.
    acceptance = _positive_binding()
    assert acceptance.resolved.contract_identity == {
        "profile_id": CODEX_ACP_1_1_7.profile_id,
        "profile_revision": CODEX_ACP_1_1_7.revision,
        "adapter_contract_hash": CODEX_ACP_1_1_7.adapter_contract_hash(),
    }

    workspace = Path(_env("ARS_CODEX_ACCEPTANCE_WORKSPACE_PARENT")) / "p0-ws"
    workspace.mkdir(parents=True, exist_ok=True)
    assembler = RunSpecAssembler(
        AgentRunRequest(**{**_typed_request(CODEX_ACP_1_1_7.profile_id)})
    )
    assembler.resolve_profile(ProfileRegistry((CODEX_ACP_1_1_7,)))
    assembler.bind_workspace(root=workspace)
    launch = assembler.resolve_launch(runtime=acceptance)
    spec = assembler.seal(
        run_id="run-p0-acceptance", submitted_at="2026-07-24T00:00:00+00:00"
    )
    # The generation's deployment facts are bound into the launch seal, and the
    # profile hash stays the registered one — the split is real in both
    # directions: source identity unchanged, deployment identity sealed.
    provenance = launch.runtime_provenance
    assert provenance.generation_id == acceptance.resolved.generation_id
    assert provenance.slot_set_hash == acceptance.resolved.slot_set_hash
    assert spec.agent.profile_hash == CODEX_ACP_1_1_7.profile_hash()
    assert spec.launch_spec_hash == launch.launch_hash()
    hashes = {
        "profile_hash": CODEX_ACP_1_1_7.profile_hash(),
        "adapter_contract_hash": CODEX_ACP_1_1_7.adapter_contract_hash(),
        "launch_hash": launch.launch_hash(),
        "spec_hash": spec_hash(spec),
        "generation_id": acceptance.resolved.generation_id,
        "slot_set_hash": acceptance.resolved.slot_set_hash,
    }
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


@pytest.mark.parametrize("leg", list(POSITIVE_LEGS))
def test_positive_legs(tmp_path: Path, leg: str) -> None:
    _codex_preflight()
    binding = _commit_binding()
    acceptance = _positive_binding()
    # The staged home is the operator-promoted generation's config-root slot,
    # not a source constant this harness invented.
    home = Path(acceptance.resolved.slot("codex_home").descriptor["path"])
    real_root = Path(_env("ARS_CODEX_ACCEPTANCE_REAL_CREDENTIAL_ROOT"))
    real_auth = real_root / "auth.json"

    # Staging, the leg, cleanup, and the nanosecond pre/post identity check all
    # live inside one guard: a staging failure cleans and re-inventories
    # exactly like a leg failure does.
    with _credential_isolation(home, real_auth, real_root):
        workspace = (
            Path(_env("ARS_CODEX_ACCEPTANCE_WORKSPACE_PARENT")) / f"{leg}-ws"
        )
        workspace.mkdir(parents=True)
        _assert_ancestor_chain_clean(workspace)
        assert sorted(entry.name for entry in workspace.iterdir()) == []

        daemon = EphemeralDaemon(
            socket_path=Path(_env("ARS_CODEX_ACCEPTANCE_SOCKET_DIR")) / f"{leg}.sock",
            supervisor_root=tmp_path / "pos-root",
            registry=ProfileRegistry((CODEX_ACP_1_1_7,)),
            binding_root=Path(_env("ARS_CODEX_ACCEPTANCE_BINDING_ROOT")),
            binding_ownership=_acceptance_ownership(),
        )
        outcome = _run_positive_leg(
            daemon,
            leg,
            workspace,
            home,
            commit_sha=binding["commit_sha"],
            acceptance=acceptance,
        )
        outcome["commit_sha"] = binding["commit_sha"]
        EVIDENCE["legs"][leg] = outcome  # type: ignore[index]


def _run_positive_leg(
    daemon: EphemeralDaemon,
    leg: str,
    workspace: Path,
    home: Path,
    *,
    commit_sha: str,
    acceptance: rb.AdmittedRuntimeBinding,
) -> dict:
    """Drive one positive leg over the private socket against the real pair.

    Returns sanitized summary facts only — never model text, session IDs,
    credential values, or credential-file digests.
    """
    derived_profile = CODEX_ACP_1_1_7
    nonce = _context_nonce(leg, commit_sha)
    continuity = leg == "p2_continuity_and_b1_boundary"
    # P2 binds a reusable Session up front: an ephemeral record is closed at
    # its own terminal, so `session/load` continuity can only be proven on a
    # named session id carried across both Runs.
    session_id = f"sess-{leg}-{commit_sha[:12]}"
    home_before = _home_state(home)
    request_overrides: dict[str, object] = {}
    if continuity:
        request_overrides = {
            "session_reuse": "reuse",
            "ars_session_id": session_id,
        }

    if leg == P4_TIMEOUT_LEG:
        # Bounds only the post-dispatch prompt await; admission, spawn, and the
        # startup sequence keep their own (default) budgets, so this limit
        # cannot fire before dispatch.
        request_overrides = {"limits": {"turn_timeout_seconds": P4_TURN_TIMEOUT_SECONDS}}

    with _serving(daemon):
        run_id = _submit(
            daemon,
            request_id=f"req-{leg}-1",
            payload=_submit_payload(
                POSITIVE_PROFILE_ID,
                workspace,
                request_overrides=request_overrides,
                prompt_text=_positive_prompt(leg, nonce),
            ),
        )
        run_dir = daemon.run_dir(run_id)
        if leg == P4_CANCEL_LEG:
            # Cancel strictly after the dispatch boundary: the prompt frame is
            # on the wire, so this is the post-dispatch row, never a 0-Turn one.
            _await_marker(run_dir, DISPATCH_STARTED_MARKER)
            _await_marker(run_dir, PROMPT_ACCEPTED_MARKER)
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

        # Common to every positive leg: the boundary attested clean, the sealed
        # identity is durable in launch.json, and the generation the Run was
        # admitted under is durable beside it.
        assert attestation["pass"] is True
        assert all(row["passed"] for row in attestation["checks"])
        rows = {row["name"] for row in attestation["checks"]}
        assert {
            "config_toml_absence_recheck",
            "project_config_closure_recheck",
            "cli_artifact_trust",
            "cli_package_closure",
            "cli_package_closure_recheck",
        } <= rows
        assert initialize["pass"] is True
        sealed = launch["expected_runtime"]
        downstream = acceptance.resolved.slot("downstream_cli").descriptor
        assert sealed["node_path"] == (
            derived_profile.contract.wrapped_runtime.interpreter_path
        )
        assert sealed["cli"]["path"] == downstream["launcher_path"]
        assert sealed["cli"]["tree_sha256"] == downstream["tree_sha256"]
        assert sealed["credential_root_path"] == str(home)
        assert dict(tuple(pair) for pair in launch["fixed_env"])["CODEX_HOME"] == str(
            home
        )
        assert launch["runtime_provenance"]["generation_id"] == (
            acceptance.resolved.generation_id
        )
        assert launch["runtime_provenance"]["adapter_contract_hash"] == (
            derived_profile.adapter_contract_hash()
        )
        assert spec["agent"]["profile_hash"] == derived_profile.profile_hash()
        assert effective["agent_info"]["name"] == (
            derived_profile.contract.acp_agent_name
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
            assert _no_redaction(run_dir), "P1 evidence was redacted"
            # Exactly the current Turn's text, checked against a token this
            # harness chose before the Run — never merely non-empty.
            _assert_exact_nonce_recall(result["final_message"], nonce)
            _assert_current_turn_message_path(_events(run_dir), result["final_message"])
            _assert_no_prompt_replay(_events(run_dir), run_dir)
            # No credential bytes and no credential-file digests in evidence.
            for artifact in ("attestation.json", "initialize_attestation.json",
                             "launch.json", "spec.json", "effective.json"):
                raw = (run_dir / artifact).read_bytes()
                assert b"auth.json" not in raw
            summary["final_message_bytes"] = len(result["final_message"].encode())
            summary["truncated"] = result["truncated"]

        elif leg == "p2_continuity_and_b1_boundary":
            assert result["status"] == "completed"
            # Run 1 must itself answer with the exact token: continuity can
            # only be claimed against a Run 1 whose current-Turn semantics were
            # exact, never against whatever text came back.
            assert _no_redaction(run_dir), "Run 1 evidence was redacted"
            _assert_exact_nonce_recall(result["final_message"], nonce)
            _assert_current_turn_message_path(_events(run_dir), result["final_message"])
            home_after_first = _home_state(home)
            new_entries = _assert_new_thread_state(home_before, home_after_first)

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
                    prompt_text=RECALL_PROMPT,
                ),
            )
            second_dir = daemon.run_dir(second_id)
            second = _await_terminal(second_dir)
            assert second["status"] == "completed"
            assert second["truncated"] is False
            assert _no_redaction(second_dir), "Run 2 evidence was redacted"
            # Real session/load, exact recall — no substring and no always-true
            # disjunction can satisfy this.
            second_events = _events(second_dir)
            assert any(
                event.get("type") == "session_load_requested"
                for event in second_events
            ), "Run 2 did not take the session/load path"
            _assert_exact_nonce_recall(second["final_message"], nonce)
            # B1: replayed history stays visible as normalized events but
            # contributes zero bytes to the current Turn's final message.
            message_path = _assert_current_turn_message_path(
                second_events, second["final_message"]
            )
            _assert_no_prompt_replay(second_events, second_dir)
            record = daemon.session_store.open_session(session_id)
            assert record.native_profile_hash == derived_profile.profile_hash()
            assert record.agent_session_id, "Run 1 never bound an external session"
            # The isolated home carries CLI-persisted thread/rollout state that
            # did not exist before Run 1 — the direct proof of why D12
            # isolation is required. Names and contents never enter evidence.
            summary["reused_session_profile_hash_bound"] = True
            summary["isolated_home_new_entries_after_first_run"] = new_entries
            summary["isolated_home_new_entries_after_reuse"] = (
                _assert_new_thread_state(home_before, _home_state(home))
            )
            summary["nonce_recalled_exactly"] = True
            summary.update(message_path)

        elif leg == "p3_permission_denied_before_effect":
            mediation = json.loads(
                (run_dir / "permission-evidence.json").read_text(encoding="utf-8")
            ) if (run_dir / "permission-evidence.json").exists() else None
            events = _events(run_dir)
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

        else:  # the two P4 sublegs
            # Both drive a *dispatched* Turn to a supervisor-forced end with no
            # trustworthy ACP terminal, and each must land on its own exact
            # B2-fixed row — never a permissive terminal set.
            _assert_p4_outcome(leg, result)
            events = _events(run_dir)
            _assert_no_prompt_replay(events, run_dir)
            assert (run_dir / PROMPT_ACCEPTED_MARKER).exists(), (
                "the prompt frame never reached the wire: this is not a "
                "post-dispatch leg"
            )
            identity = effective.get("process_identity")
            assert identity and identity.get("pid"), (
                "no process identity recorded: the child never spawned"
            )
            _assert_process_group_gone(identity)
            record = daemon.session_store.open_session(
                _session_id_for(daemon, run_id)
            )
            assert record.state == P4_EXPECTED_OUTCOMES[leg]["session_state"]
            summary["process_group_gone"] = True
            summary["prompt_dispatched_exactly_once"] = True
            summary["session_state"] = record.state
            summary["subleg"] = leg

        return summary


def _events(run_dir: Path) -> list[dict]:
    path = run_dir / "events.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _no_redaction(run_dir: Path) -> bool:
    """Redaction would break the length equality the B1 boundary relies on."""
    report = run_dir / "redaction-report.json"
    if not report.exists():
        return True
    return not json.loads(report.read_text(encoding="utf-8"))["matches"]


def _positive_prompt(leg: str, nonce: str) -> str:
    if leg in NONCE_LEGS:
        # R8 P1/P2: prompt with the context nonce so the Turn's own result can
        # be checked exactly, not merely for non-emptiness.
        return _nonce_prompt(nonce)
    if leg == "p3_permission_denied_before_effect":
        # Two privileged canaries under a read-only grant: each must produce a
        # real session/request_permission, be denied, and leave no sentinel.
        return (
            "Do both of the following in the current working directory: "
            "first create a file named edit-canary.txt containing the word "
            "canary; then run a shell command that creates execute-canary.txt."
        )
    if leg == P4_TIMEOUT_LEG:
        # Deliberately unbounded work so the Turn cannot finish inside the
        # short post-dispatch limit; a completed Turn FAILs this leg.
        return (
            "Count slowly from 1 to 100000, writing out every number in words, "
            "one per line, without stopping or summarizing."
        )
    # P4 cancel: long-running by construction, so the cancel lands mid-Turn.
    return (
        "Describe, at length and without stopping, every step you would take "
        "to review a large repository."
    )


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


@pytest.mark.parametrize("case", NEGATIVE_CASES, ids=lambda case: case.case_id)
def test_negative_legs(tmp_path: Path, case: NegativeCase) -> None:
    _codex_preflight()
    binding = _commit_binding()
    fixtures = PrivateNegativeFixtures(tmp_path / "neg")
    negative_profile = fixtures.profile()
    daemon = EphemeralDaemon(
        socket_path=(
            Path(_env("ARS_CODEX_ACCEPTANCE_SOCKET_DIR")) / f"{case.case_id}.sock"
        ),
        supervisor_root=tmp_path / "neg-root",
        registry=ProfileRegistry((negative_profile,)),
        binding_root=fixtures.write_binding_root(negative_profile),
        binding_ownership=fixtures.binding_ownership(),
    )
    outcome = _run_negative_leg(daemon, fixtures, case)
    outcome["commit_sha"] = binding["commit_sha"]
    EVIDENCE["legs"][case.case_id] = outcome  # type: ignore[index]


def _arrange_negative_case(
    case_id: str, fixtures: "PrivateNegativeFixtures"
) -> dict[str, object]:
    """Arrange exactly one tampered surface on the private copies.

    Pure with respect to the daemon and the wire: it mutates only the private
    fixture tree and returns the caller-request overrides the probe Run needs,
    so the hermetic contract suite can drive every declared variant against
    synthetic fixtures without a socket, a spawn, or a credential.
    """
    stage = fixtures.base / "stage"
    overrides: dict[str, object] = {}

    if case_id == "n1_tampered_adapter_entry":
        fixtures.entry.write_text("// tampered\n", encoding="utf-8")
    elif case_id == "n2_swapped_node_binary":
        swapped = stage / "node-swapped"
        swapped.write_bytes(b"#!/bin/false\n# swapped node\n")
        os.replace(swapped, fixtures.node)
    elif case_id == "n3_retargeted_cli_symlink":
        other = stage / "codex-other"
        other.write_bytes(b"#!/bin/false\n# retargeted cli\n")
        fixtures.cli.unlink()
        fixtures.cli.symlink_to(other)
    elif case_id == "n4_auth_json_symlink":
        target = stage / "elsewhere-auth.json"
        target.write_bytes(SYNTHETIC_AUTH_BYTES)
        os.chmod(target, 0o600)
        fixtures.auth.unlink()
        fixtures.auth.symlink_to(target)
    elif case_id == "n4_auth_json_mode_0644":
        os.chmod(fixtures.auth, 0o644)
    elif case_id == "n4_auth_json_removed":
        fixtures.auth.unlink()
    elif case_id == "n5_credential_root_mode_0750":
        os.chmod(fixtures.cred_root, 0o750)
    elif case_id == "n5_credential_root_symlink":
        relocated = fixtures.base / "relocated-synthetic-home"
        relocated.mkdir(mode=0o700)
        os.chmod(relocated, 0o700)
        relocated_auth = relocated / "auth.json"
        relocated_auth.write_bytes(SYNTHETIC_AUTH_BYTES)
        os.chmod(relocated_auth, 0o600)
        fixtures.auth.unlink()
        fixtures.cred_root.rmdir()
        fixtures.cred_root.symlink_to(relocated, target_is_directory=True)
    elif case_id == "n6_credential_root_config_toml":
        (fixtures.cred_root / "config.toml").write_text(
            "[features]\n", encoding="utf-8"
        )
    elif case_id == "n7_project_config_at_cwd":
        # cwd is a subdirectory of the workspace root, so only the *cwd* layer
        # exists: the walk must still find it at its own level.
        overrides["cwd"] = str(fixtures.nested_cwd)
        _write_project_layer(fixtures.nested_cwd)
    elif case_id == "n7_project_config_at_workspace_root":
        overrides["cwd"] = str(fixtures.nested_cwd)
        _write_project_layer(fixtures.workspace)
    elif case_id == "n7_project_config_above_workspace_root":
        overrides["cwd"] = str(fixtures.nested_cwd)
        _write_project_layer(fixtures.workspace.parent)
    elif case_id == "n7_project_config_inserted_between_runs":
        # Arranged after the seeding Run: a layer deposited between Runs must
        # be refused at the next spawn boundary on the same Session.
        _write_project_layer(fixtures.workspace)
    elif case_id == "n8_credential_refs_missing":
        overrides["credential_refs"] = []
    elif case_id == "n8_credential_refs_wrong":
        overrides["credential_refs"] = ["not-the-registered-slot"]
    elif case_id == "n8_credential_refs_extra":
        overrides["credential_refs"] = ["codex-home-auth", "codex-home-auth-2"]
    elif case_id in {
        "n9_seeded_session_profile_hash_drift",
        "n9_quarantined_session_reuse",
    }:
        # Both are arranged on the Session, not on the fixture tree; see
        # _run_negative_leg.
        pass
    else:  # pragma: no cover - the contract suite forbids reaching this
        raise AssertionError(f"undeclared negative case {case_id!r}")
    return overrides


def _write_project_layer(directory: Path) -> Path:
    layer = directory / ".codex"
    layer.mkdir(parents=True, exist_ok=True)
    config = layer / "config.toml"
    config.write_text("model = 'poison'\n", encoding="utf-8")
    return config


def _run_negative_leg(
    daemon: EphemeralDaemon,
    fixtures: "PrivateNegativeFixtures",
    case: NegativeCase,
) -> dict:
    """Mutate private copies only, then prove the refusal and the no-spawn fact.

    Seeded legs (the N7 insertion-between-Runs case and both N9 cases) first
    bind a reusable ARS Session by running the harness-written codex-shaped
    fake ``.mjs`` agent under the private Node copy — legitimately passing
    attestation with zero real credentials, zero real adapter execution, and
    zero model calls. The seed uses a *named* session id, because an ephemeral
    record is closed at its own terminal and could never be reused.
    """
    os.environ["FAKE_AGENT_NAME"] = fixtures.expected().agent_info_name
    os.environ["FAKE_AGENT_VERSION"] = fixtures.expected().agent_info_version
    pre_state = _private_fixture_state(fixtures)
    seeded_session = f"sess-{case.case_id}" if case.seeded else None
    seed_cwd = str(fixtures.nested_cwd) if case.family == "n7" else None

    with _serving(daemon):
        if seeded_session is not None:
            seed_id = _submit(
                daemon,
                request_id=f"req-{case.case_id}-seed",
                payload=_submit_payload(
                    NEGATIVE_PROFILE_ID,
                    fixtures.workspace,
                    cwd=seed_cwd,
                    request_overrides={
                        "session_reuse": "reuse",
                        "ars_session_id": seeded_session,
                    },
                ),
            )
            seed = _await_terminal(daemon.run_dir(seed_id))
            assert seed["status"] == "completed", "session seeding must pass cleanly"
            record = daemon.session_store.open_session(seeded_session)
            assert record.agent_session_id, "seeding never bound an external session"

        request_overrides = _arrange_negative_case(case.case_id, fixtures)
        arranged_state = _private_fixture_state(fixtures)
        probe_cwd = request_overrides.pop("cwd", None)
        if seeded_session is not None:
            request_overrides["session_reuse"] = "reuse"
            request_overrides["ars_session_id"] = seeded_session
            probe_cwd = probe_cwd or seed_cwd
        if case.case_id == "n9_seeded_session_profile_hash_drift":
            # A revision bump in the private registry alone must refuse reuse
            # before spawn: the record's bound profile hash no longer matches.
            drifted = fixtures.profile(revision=2)
            daemon.handlers._factory = handlers.default_run_task_factory(
                daemon.supervisor_root,
                registry=ProfileRegistry((drifted,)),
                # A revision bump changes the contract hash, so the drifted
                # registry needs its own generation; otherwise the Binding read
                # would refuse first and the *session* refusal under test would
                # never be reached.
                binding_root=fixtures.write_binding_root(drifted),
                binding_ownership=fixtures.binding_ownership(),
            )
        if case.case_id == "n9_quarantined_session_reuse":
            daemon.session_store.write_quarantine_pending(
                seeded_session, reason="acceptance-arranged", run_id="n9-arrange"
            )
            daemon.session_store.mark_quarantined(
                seeded_session, reason="acceptance-arranged", run_id="n9-arrange"
            )

        run_id = _submit(
            daemon,
            request_id=f"req-{case.case_id}-probe",
            payload=_submit_payload(
                NEGATIVE_PROFILE_ID,
                fixtures.workspace,
                cwd=probe_cwd,
                request_overrides=request_overrides,
            ),
        )
        run_dir = daemon.run_dir(run_id)
        result = _await_terminal(run_dir)

    assert result["status"] == "failed"
    assert result["detail_code"] == case.detail_code, result
    assert result["retryable"] is False
    _assert_never_spawned(run_dir)

    if case.failing_row is not None:
        assert case.stage == "attestation"
        rows = _attestation_rows(run_dir)
        assert rows[case.failing_row]["passed"] is False, case.failing_row
        assert all(
            row["passed"] for name, row in rows.items() if name != case.failing_row
        ), "exactly one attestation row may fail"
    else:
        # Binding, admission, and session-binding refusals never reach the
        # attestation stage, so the probe Run carries no attestation artifact.
        assert case.stage in {"binding", "admission", "session"}
        assert not (run_dir / "attestation.json").exists()
    if case.stage == "binding":
        # Refused before the RunTask exists: not even a sealed spec was written.
        assert not (run_dir / "spec.json").exists()
        assert not (run_dir / "launch.json").exists()

    # Private fixtures stay exactly as arranged — the refusal path mutated
    # nothing — and nothing real was ever referenced.
    post_state = _private_fixture_state(fixtures)
    assert post_state == arranged_state
    assert set(post_state) == set(pre_state)
    return {
        "case_id": case.case_id,
        "family": case.family,
        "stage": case.stage,
        "detail_code": result["detail_code"],
        "failing_row": case.failing_row,
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
    """Durable trees byte-untouched; the production surface never referenced.

    Two halves, checked against their own authority: the interpreter and the
    ACP adapter entry against the source contract, and the downstream CLI
    closure against the operator's promoted generation.
    """
    _codex_preflight()
    wrapped = CODEX_ACP_1_1_7.contract.wrapped_runtime
    assert wrapped is not None
    for path, expected_digest in (
        (Path(wrapped.interpreter_path), wrapped.interpreter_sha256),
        (Path(wrapped.adapter_entry_path), wrapped.adapter_entry_sha256),
    ):
        assert _sha256_file(path) == expected_digest, path
    downstream = _positive_binding().resolved.slot("downstream_cli").descriptor
    assert _sha256_file(Path(downstream["launcher_path"])) == (
        downstream["launcher_sha256"]
    )
    assert rb.package_tree_digest(Path(downstream["package_root"])) == (
        downstream["tree_sha256"]
    )
    EVIDENCE["shared_resources_verified"] = True


def test_evidence_bundle_is_sanitized_and_sha_bound() -> None:
    """A bundle without the D14 SHA binding is incomplete and FAILs the gate."""
    _codex_preflight()
    binding = _commit_binding()
    assert binding["commit_sha"]
    serialized = json.dumps(EVIDENCE, sort_keys=True)
    for banned in ("auth.json\":", "BEGIN PRIVATE KEY", "Bearer "):
        assert banned not in serialized

    # Completeness: every declared leg — both P4 sublegs and every R8 N1–N9
    # variant — contributed its own distinct sanitized entry bound to the
    # reviewed SHA. A silently missing leg makes the bundle incomplete.
    legs = EVIDENCE["legs"]
    assert isinstance(legs, dict)
    assert set(legs) == set(POSITIVE_LEGS) | {
        case.case_id for case in NEGATIVE_CASES
    }
    assert all(entry["commit_sha"] == binding["commit_sha"] for entry in legs.values())

    assert DISPATCH_STARTED_MARKER  # imported contract marker stays referenced
    assert arsd_client.ArsdClient is not None
    assert profile_module._REGISTERED_EXECUTABLES["codex-acp"]
